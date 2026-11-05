import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import RecipeTile from "../components/RecipeTile";
import { DateBadge, Empty, ErrorNote, Loading, Media, useApi } from "../components/ui";
import { useAuth } from "../lib/auth";
import { fmtDayLong, isoToday } from "../lib/format";
import { REASONS } from "./Plan";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Today() {
  const { me } = useAuth();
  const { data, error, loading, reload } = useApi<any>("/today");
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">{fmtDayLong(data?.date || new Date().toISOString())}</div>
          <h1>{greeting()}, {me?.user.display_name.split(" ")[0]}</h1>
          {data?.away?.length > 0 && <AwayLine away={data.away} />}
        </div>
        <div className="row-wrap">
          <Link className="btn" to="/pantry/scan"><Icon name="scan" />Scan item</Link>
          <Link className="btn" to="/recipes/new"><Icon name="link" />Add recipe</Link>
          <Link className="btn" to="/receipts?upload=1"><Icon name="receipt" />Review receipt</Link>
        </div>
      </div>
      {error && <ErrorNote error={error} retry={reload} />}
      {loading && !data && <Loading lines={5} />}
      {data && (
        <div className="today-grid">
          <div className="today-main">
            <NextMeal meal={data.next_meal} />
            <div className="section-title"><h2>What can we cook?</h2><Link to="/recipes?have=1" className="small">See all</Link></div>
            {data.ideas.length === 0 ? (
              <Empty icon="book" title="No suggestions yet">Add a few recipes and some pantry stock, and ideas that use what you have will appear here.</Empty>
            ) : (
              <div className="scroll-x ideas">{data.ideas.map((r: any) => <RecipeTile key={r.id} r={r} showMatch width={232} />)}</div>
            )}
          </div>
          <div>
            <div className="section-title" style={{ marginTop: 0 }}><h2>Use soon</h2><Link to="/pantry" className="small">Pantry</Link></div>
            {data.use_soon.length === 0 ? <p className="muted small">Nothing is close to its date.</p> : (
              <ul className="list">
                {data.use_soon.map((u: any) => (
                  <li key={u.lot_id}>
                    <Link to={`/pantry?lot=${u.lot_id}`} className="list-row" style={{ color: "inherit", textDecoration: "none" }}>
                      <div className="grow">
                        <div className="title">{u.name}</div>
                        <div className="meta"><span className="num">{u.display}</span> · {u.location}</div>
                      </div>
                      <DateBadge kind={u.date_kind} date={u.date} label={u.date_label} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <div className="section-title"><h2>Running low</h2><Link to="/shopping" className="small">Shopping list</Link></div>
            {data.low_stock.length === 0 ? <p className="muted small">Nothing is below its minimum. Set minimums on pantry items to be reminded.</p> : (
              <ul className="list">
                {data.low_stock.map((l: any) => (
                  <li key={l.id} className="list-row">
                    <div className="grow"><div className="title">{l.name}</div><div className="meta num">{l.have} left · minimum {l.minimum}</div></div>
                    <span className="tag ochre">Low</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function AwayLine({ away }: { away: any[] }) {
  const t = isoToday();
  const parts = away.map((a) => {
    const today = a.start_date <= t && t <= a.end_date;
    const day = today ? "today" : "tomorrow";
    const when = a.all_day ? `all day ${day}` : a.slots.length === 1 && a.slots[0] === "dinner" && today ? "tonight" : `for ${a.slots.join(" and ")} ${day}`;
    return `${a.person_name} is out ${when} (${(REASONS[a.reason] || a.reason).toLowerCase()}${a.note ? `: ${a.note}` : ""})`;
  });
  return <p className="small" style={{ margin: "6px 0 0" }}><Link to="/plan" style={{ color: "var(--ink-2)" }}>{parts.join(" · ")}</Link></p>;
}

function NextMeal({ meal }: { meal: any }) {
  if (!meal) {
    return (
      <div className="panel">
        <div className="eyebrow">Next meal</div>
        <h2 style={{ margin: "6px 0 8px" }}>Nothing planned yet</h2>
        <p className="muted small">Plan the week and the shopping list will work out what you need, minus what's already in.</p>
        <Link to="/plan" className="btn primary">Plan meals</Link>
      </div>
    );
  }
  const req = meal.requirements || [];
  const missing = req.filter((r: any) => r.status === "missing" || r.status === "short");
  return (
    <article className="next-meal">
      <Media src={meal.hero?.url} alt={meal.title} eager ratio="ratio-hero" focal={meal.hero ? [meal.hero.focal_x, meal.hero.focal_y] : undefined} />
      <div>
        <div className="eyebrow">Next meal · {meal.slot} {meal.date === new Date().toLocaleDateString("en-CA") ? "tonight" : ""}</div>
        <h2 style={{ fontSize: "1.875rem", margin: "6px 0 10px" }}>{meal.title}</h2>
        <p className="muted">For {meal.servings} servings{meal.version_label && meal.version_label !== "Original" ? ` · ${meal.version_label}` : ""}.</p>
        {missing.length === 0 ? (
          <p className="small"><span className="tag herb">All ingredients in</span></p>
        ) : (
          <p className="small"><span className="tag tomato">Missing {missing.length}</span> {missing.map((m: any) => `${m.name}${m.missing_display ? ` (${m.missing_display})` : ""}`).join(", ")}</p>
        )}
        <div className="row-wrap">
          {meal.recipe_id && <Link className="btn primary" to={`/recipes/${meal.recipe_id}/cook?servings=${meal.servings}&plan=${meal.id}&v=${meal.recipe_version_id}`}><Icon name="pot" />Start cooking</Link>}
          {meal.recipe_id && <Link className="btn" to={`/recipes/${meal.recipe_id}?servings=${meal.servings}&v=${meal.recipe_version_id}`}>View recipe</Link>}
        </div>
      </div>
    </article>
  );
}
