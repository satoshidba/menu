"""Deterministic recipe extraction: schema.org/Recipe JSON-LD and microdata, then plain-text heuristics.

All imported text is reduced to plain text (no HTML is ever stored or rendered).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from .units import normalise_temperatures, parse_ingredient_line


def plain_text(value) -> str:
    """Strip tags/entities and collapse whitespace."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(plain_text(v) for v in value)
    if isinstance(value, dict):
        return plain_text(value.get("text") or value.get("name") or "")
    s = str(value)
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</li>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self._in_ld = False
        self._buf: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.micro: dict[str, list] = {}
        self._micro_prop: str | None = None
        self._micro_text: list[str] = []
        self.text_chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld, self._buf = True, []
            return
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        prop = a.get("itemprop")
        if prop:
            if tag in ("meta", "link"):
                val = a.get("content") or a.get("href")
                if val:
                    self.micro.setdefault(prop, []).append(val)
            elif tag == "img" and a.get("src"):
                self.micro.setdefault(prop, []).append(a["src"])
            elif tag == "time" and a.get("datetime"):
                self.micro.setdefault(prop, []).append(a["datetime"])
            else:
                self._micro_prop, self._micro_text = prop, []
        if tag in ("p", "li", "br", "h1", "h2", "h3", "h4", "div", "tr"):
            self.text_chunks.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.jsonld.append("".join(self._buf))
            self._in_ld = False
            return
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if self._micro_prop and tag in ("li", "span", "div", "p", "h1", "h2", "a", "ol", "section"):
            txt = " ".join(self._micro_text).strip()
            if txt:
                self.micro.setdefault(self._micro_prop, []).append(txt)
            self._micro_prop = None

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)
            return
        if self._in_title:
            self.title += data
        if self._micro_prop:
            self._micro_text.append(data.strip())
        if not self._skip:
            self.text_chunks.append(data)


@dataclass
class ExtractedRecipe:
    title: str | None = None
    description: str | None = None
    author: str | None = None
    site_name: str | None = None
    image_urls: list[str] = field(default_factory=list)
    ingredients: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)  # {"text":..., "image": url|None, "section": str|None}
    servings: float | None = None
    yield_text: str | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    cuisine: str | None = None
    tags: list[str] = field(default_factory=list)
    nutrition_source: dict = field(default_factory=dict)
    method: str = "none"  # json-ld | microdata | heuristic | none
    page_text: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.title and self.ingredients and self.steps)


def _iso_minutes(v) -> int | None:
    if not v or not isinstance(v, str):
        return None
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", v.strip(), re.I)
    if not m:
        return None
    d, h, mi, _ = (int(x) if x else 0 for x in m.groups())
    total = d * 1440 + h * 60 + mi
    return total or None


def _find_recipe(node):
    if isinstance(node, list):
        for n in node:
            r = _find_recipe(n)
            if r:
                return r
        return None
    if not isinstance(node, dict):
        return None
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    if any(isinstance(x, str) and x.lower() == "recipe" for x in types):
        return node
    for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement", "item"):
        if key in node:
            r = _find_recipe(node[key])
            if r:
                return r
    return None


def _images(value, base: str) -> list[str]:
    out = []
    if value is None:
        return out
    if isinstance(value, str):
        out.append(urljoin(base, value))
    elif isinstance(value, dict):
        u = value.get("url") or value.get("contentUrl") or value.get("@id")
        if isinstance(u, str):
            out.append(urljoin(base, u))
    elif isinstance(value, list):
        for v in value:
            out.extend(_images(v, base))
    return [u for u in out if u.startswith("http")]


def _steps(value, base: str, section: str | None = None) -> list[dict]:
    out: list[dict] = []
    if value is None:
        return out
    if isinstance(value, str):
        for part in re.split(r"\n+|(?<=\.)\s{2,}", plain_text(value)):
            part = part.strip()
            if part:
                out.append({"text": part, "image": None, "section": section})
        return out
    if isinstance(value, list):
        for v in value:
            out.extend(_steps(v, base, section))
        return out
    if isinstance(value, dict):
        t = value.get("@type")
        if t == "HowToSection" or "itemListElement" in value:
            return _steps(value.get("itemListElement"), base, plain_text(value.get("name")) or section)
        text = plain_text(value.get("text") or value.get("name") or value.get("description"))
        if text:
            imgs = _images(value.get("image"), base)
            out.append({"text": text, "image": imgs[0] if imgs else None, "section": section})
    return out


def _servings(value) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    vals = value if isinstance(value, list) else [value]
    text = None
    for v in vals:
        s = plain_text(v)
        text = text or s
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if m:
            return float(m.group(1)), s
    return None, text


def extract_from_html(html_text: str, base_url: str) -> ExtractedRecipe:
    p = _PageParser()
    try:
        p.feed(html_text)
    except Exception:  # malformed HTML: use whatever was gathered
        pass
    er = ExtractedRecipe()
    er.site_name = p.meta.get("og:site_name")
    er.page_text = re.sub(r"\n\s*\n+", "\n", "".join(p.text_chunks))[:60000]
    og_image = p.meta.get("og:image")

    recipe = None
    for raw in p.jsonld:
        try:
            data = json.loads(raw.strip().rstrip(";"))
        except json.JSONDecodeError:
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", raw))
            except json.JSONDecodeError:
                continue
        recipe = _find_recipe(data)
        if recipe:
            break

    if recipe:
        er.method = "json-ld"
        er.title = plain_text(recipe.get("name")) or None
        er.description = plain_text(recipe.get("description")) or None
        author = recipe.get("author")
        if isinstance(author, list) and author:
            author = author[0]
        er.author = plain_text(author.get("name") if isinstance(author, dict) else author) or None
        er.image_urls = _images(recipe.get("image"), base_url)
        ings = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
        er.ingredients = [plain_text(i) for i in (ings if isinstance(ings, list) else [ings]) if plain_text(i)]
        er.steps = _steps(recipe.get("recipeInstructions"), base_url)
        er.servings, er.yield_text = _servings(recipe.get("recipeYield"))
        er.prep_minutes = _iso_minutes(recipe.get("prepTime"))
        er.cook_minutes = _iso_minutes(recipe.get("cookTime"))
        er.total_minutes = _iso_minutes(recipe.get("totalTime"))
        cu = recipe.get("recipeCuisine")
        er.cuisine = plain_text(cu[0] if isinstance(cu, list) and cu else cu) or None
        kw = recipe.get("keywords")
        if isinstance(kw, str):
            er.tags = [k.strip() for k in kw.split(",") if k.strip()][:12]
        elif isinstance(kw, list):
            er.tags = [plain_text(k) for k in kw][:12]
        cat = recipe.get("recipeCategory")
        if cat:
            er.tags = list(dict.fromkeys(er.tags + [plain_text(c) for c in (cat if isinstance(cat, list) else [cat])]))[:12]
        if isinstance(recipe.get("nutrition"), dict):
            er.nutrition_source = {k: plain_text(v) for k, v in recipe["nutrition"].items() if not k.startswith("@")}
    elif p.micro.get("recipeIngredient") or p.micro.get("ingredients"):
        er.method = "microdata"
        er.title = (p.micro.get("name") or [p.title])[0]
        er.ingredients = [plain_text(x) for x in (p.micro.get("recipeIngredient") or p.micro.get("ingredients") or [])]
        er.steps = [{"text": plain_text(x), "image": None, "section": None} for x in p.micro.get("recipeInstructions", [])]
        er.image_urls = _images(p.micro.get("image"), base_url)
        er.servings, er.yield_text = _servings(p.micro.get("recipeYield"))
        er.prep_minutes = _iso_minutes((p.micro.get("prepTime") or [None])[0])
        er.cook_minutes = _iso_minutes((p.micro.get("cookTime") or [None])[0])
    else:
        heur = extract_from_text(er.page_text)
        heur.page_text, heur.site_name = er.page_text, er.site_name
        heur.title = heur.title or plain_text(p.meta.get("og:title") or p.title) or None
        er = heur
        er.method = "heuristic" if er.ingredients else "none"

    if og_image and not er.image_urls:
        er.image_urls = [urljoin(base_url, og_image)]
    er.title = er.title or plain_text(p.meta.get("og:title") or p.title) or None
    for s in er.steps:
        s["text"] = normalise_temperatures(s["text"])
    return er


_ING_HEAD = re.compile(r"^\s*(ingredients?|you will need|what you need)\s*:?\s*$", re.I)
_METHOD_HEAD = re.compile(r"^\s*(method|directions|instructions|steps|preparation|how to make( it)?)\s*:?\s*$", re.I)
_STOP_HEAD = re.compile(r"^\s*(notes?|nutrition|tips|comments?|reviews?|related|you might also like)\b", re.I)


def extract_from_text(text: str) -> ExtractedRecipe:
    """Heuristic extraction from pasted text: find Ingredients / Method sections."""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    er = ExtractedRecipe(method="text")
    section = None
    for i, ln in enumerate(lines):
        if _ING_HEAD.match(ln):
            section = "ing"
            continue
        if _METHOD_HEAD.match(ln):
            section = "method"
            continue
        if _STOP_HEAD.match(ln) and section == "method":
            section = None
            continue
        if section is None and er.title is None and len(ln) < 120 and i < 5:
            er.title = ln
            continue
        m = re.search(r"\b(serves|servings|makes)\s*:?\s*(\d+)", ln, re.I)
        if m and er.servings is None:
            er.servings = float(m.group(2))
            er.yield_text = ln[:120]
        if section == "ing":
            if len(ln) < 160:
                er.ingredients.append(re.sub(r"^[-•*·]\s*", "", ln))
        elif section == "method":
            step = re.sub(r"^(step\s*)?\d+[.):]?\s*", "", ln, flags=re.I)
            if step:
                er.steps.append({"text": normalise_temperatures(step), "image": None, "section": None})
    return er


def structure_ingredients(lines: list[str]) -> list[dict]:
    out = []
    for i, line in enumerate(lines):
        pi = parse_ingredient_line(line)
        out.append({
            "position": i, "original_text": pi.original_text, "name": pi.name, "quantity": pi.quantity,
            "quantity_max": pi.quantity_max, "unit": pi.unit, "preparation": pi.preparation, "note": pi.note,
            "optional": pi.optional, "unresolved": pi.unresolved, "confidence": pi.confidence,
        })
    return out
