# Third-party data, fonts and images

- **CoFID**: McCance and Widdowson's Composition of Foods Integrated Dataset 2021, Public Health England / OHID.
  Crown copyright, [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
  A 47-food subset is bundled unmodified in `backend/app/seed/cofid_subset.json`; the full dataset is imported from
  [gov.uk](https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid) on request.
- **Open Food Facts / Open Pet Food Facts**: product data under the
  [Open Database Licence](https://opendatacommons.org/licenses/odbl/1-0/), with images under CC BY-SA. It is fetched
  per barcode, cached separately, and shown with attribution as unverified community data.
- **Fonts**: [Newsreader](https://github.com/productiontype/Newsreader) (Production Type) and
  [IBM Plex Sans](https://github.com/IBM/plex) (IBM), both under the SIL Open Font License 1.1. They are self-hosted via
  `@fontsource` packages, and the licence files are copied to `/licences/` in the web image.
- **Sample dish photos** (`backend/app/seed/images/`, details in `CREDITS.json`), all via Wikimedia Commons:
  - `pasta.jpg`: "Roasted Chicken Penne Asiago" by Geoff Peters, CC BY 2.0
  - `dal.jpg`: "Lal Masoor Dal and Palak" by Miansari66, CC0
  - `porridge.jpg`: "Banana oatmeal" by Shisma, CC BY 4.0
  - `frittata.jpg`: "Frittata - Bam's Kitchen" by Andy Li, CC0
- **Mealie** was used only as a UX reference; no Mealie code is included.
