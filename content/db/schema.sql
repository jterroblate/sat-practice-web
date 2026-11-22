-- Normalized (3NF) question store.
--
-- Each table is one JSON Lines file beside this schema: one row per line, keys
-- in column order, rows sorted by primary key. scripts/content_db.py loads the
-- files into SQLite under these constraints; the database itself is a build
-- product and is never committed.
--
-- Every non-key column depends on the whole primary key of its table and on
-- nothing else. Facts that depend on another column live in that column's own
-- table: section on domain, domain on skill, skill on subtype, image size on
-- chart, real-exam-or-practice on the source tier.

PRAGMA foreign_keys = ON;

-- Taxonomy: section <- domain <- skill <- subtype.

-- A domain is the category a learner picks to practise: the four Reading and
-- Writing domains, the four Math domains, and one "unclassified" per section.
CREATE TABLE domain (
  domain_id TEXT PRIMARY KEY,
  section   TEXT NOT NULL CHECK (section IN ('reading_writing', 'math')),
  name      TEXT NOT NULL UNIQUE,
  name_zh   TEXT UNIQUE
) STRICT;

CREATE TABLE skill (
  skill_id  TEXT PRIMARY KEY,
  domain_id TEXT NOT NULL REFERENCES domain (domain_id),
  name      TEXT NOT NULL UNIQUE
) STRICT;

-- A question points at exactly one subtype row, so its skill, domain and
-- section are looked up, never stored twice. A NULL name is the skill's
-- "no subtype recorded" row.
CREATE TABLE subtype (
  subtype_id TEXT PRIMARY KEY,
  skill_id   TEXT NOT NULL REFERENCES skill (skill_id),
  name       TEXT,
  UNIQUE (skill_id, name)
) STRICT;

CREATE UNIQUE INDEX subtype_one_unspecified_per_skill
  ON subtype (skill_id) WHERE name IS NULL;

-- Where material comes from. The tier says how much a source counts and
-- whether it is a real exam or practice; a family is one kind of source.

CREATE TABLE source_tier (
  tier INTEGER PRIMARY KEY CHECK (tier BETWEEN 1 AND 3),
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('real_exam', 'practice'))
) STRICT;

CREATE TABLE source_family (
  family_id TEXT PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,
  tier      INTEGER NOT NULL REFERENCES source_tier (tier)
) STRICT;

-- One paper, practice test or question-bank file. family_id is NULL only for a
-- source label that arrived from a set file and has not been sorted yet.
CREATE TABLE source (
  source_id TEXT PRIMARY KEY,
  label     TEXT NOT NULL UNIQUE,
  family_id TEXT REFERENCES source_family (family_id),
  year      INTEGER CHECK (year BETWEEN 2000 AND 2100),
  month     INTEGER CHECK (month BETWEEN 1 AND 12),
  region    TEXT CHECK (region IN ('apac', 'north_america'))
) STRICT;

CREATE TABLE source_module (
  source_id    TEXT NOT NULL REFERENCES source (source_id),
  module_index INTEGER NOT NULL CHECK (module_index > 0),
  name         TEXT NOT NULL,
  minutes      INTEGER,
  PRIMARY KEY (source_id, module_index)
) STRICT;

-- One row per question, however many sets or sources it appears in.
--
-- status:      published questions are exactly those placed in a material and
--              written to content/canonical/; drafts are in no material.
-- format:      'choice' has choice rows and a correct_answer label;
--              'numeric' is a student-produced response with numeric_answer rows.
-- markup:      'canonical' text follows the site's inline markup; 'source_html'
--              is kept as imported and must be converted before publishing.
-- skill_basis: who says the subtype is right: 'curated' review, the 'source_file'
--              the question came from, a 'rule', a 'model', or 'none' (unclassified).
-- difficulty_basis: 'source_file' when the source states the difficulty,
--              'model' when it is a model's estimate from the text alone.

CREATE TABLE question (
  question_id         TEXT PRIMARY KEY,
  passage             TEXT NOT NULL,
  prompt              TEXT NOT NULL,
  correct_answer      TEXT,
  subtype_id          TEXT NOT NULL REFERENCES subtype (subtype_id),
  needs_manual_review INTEGER NOT NULL CHECK (needs_manual_review IN (0, 1)),
  -- Imported as found: 'easy', 'medium', 'hard', 'unknown' or a number.
  difficulty_estimate ANY,
  skills_tested       TEXT CHECK (skills_tested IS NULL OR json_valid(skills_tested)),
  status              TEXT NOT NULL CHECK (status IN ('published', 'draft')),
  format              TEXT NOT NULL CHECK (format IN ('choice', 'numeric')),
  markup              TEXT NOT NULL CHECK (markup IN ('canonical', 'source_html')),
  difficulty          TEXT CHECK (difficulty IN ('easy', 'medium', 'hard')),
  skill_basis         TEXT NOT NULL CHECK (skill_basis IN ('curated', 'source_file', 'rule', 'model', 'none')),
  difficulty_basis    TEXT CHECK (difficulty_basis IN ('source_file', 'model')),
  CHECK ((format = 'choice') = (correct_answer IS NOT NULL)),
  CHECK ((difficulty IS NULL) = (difficulty_basis IS NULL)),
  CHECK (status = 'draft' OR (format = 'choice' AND markup = 'canonical')),
  FOREIGN KEY (question_id, correct_answer)
    REFERENCES choice (question_id, label) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE choice (
  question_id TEXT NOT NULL REFERENCES question (question_id),
  label       TEXT NOT NULL CHECK (label IN ('A', 'B', 'C', 'D')),
  text        TEXT NOT NULL,
  analysis    TEXT,
  image       TEXT,
  PRIMARY KEY (question_id, label)
) STRICT;

CREATE TABLE numeric_answer (
  question_id TEXT NOT NULL REFERENCES question (question_id),
  position    INTEGER NOT NULL CHECK (position > 0),
  value       TEXT NOT NULL,
  PRIMARY KEY (question_id, position),
  UNIQUE (question_id, value)
) STRICT;

CREATE TABLE explanation (
  question_id  TEXT PRIMARY KEY REFERENCES question (question_id),
  short        TEXT,
  key_evidence TEXT,
  why_correct  TEXT,
  detailed     TEXT NOT NULL
) STRICT;

-- Figures. table_data and visual_assets are display documents kept whole as
-- JSON values; they are rendered, never queried by part.

CREATE TABLE chart (
  chart_image TEXT PRIMARY KEY,
  data_uri    TEXT NOT NULL,
  width       INTEGER CHECK (width > 0),
  height      INTEGER CHECK (height > 0)
) STRICT;

CREATE TABLE question_visual (
  question_id            TEXT PRIMARY KEY REFERENCES question (question_id),
  table_data             TEXT CHECK (table_data IS NULL OR json_valid(table_data)),
  visual_assets          TEXT CHECK (visual_assets IS NULL OR json_valid(visual_assets)),
  chart_image            TEXT REFERENCES chart (chart_image),
  public_visual_alt_text TEXT
) STRICT;

-- Where a question sat in an imported source: module and number within it.
CREATE TABLE question_source (
  source_id    TEXT NOT NULL,
  module_index INTEGER NOT NULL,
  number       INTEGER NOT NULL CHECK (number > 0),
  question_id  TEXT NOT NULL REFERENCES question (question_id),
  raw_item_id  INTEGER NOT NULL UNIQUE,
  PRIMARY KEY (source_id, module_index, number),
  FOREIGN KEY (source_id, module_index) REFERENCES source_module (source_id, module_index)
) STRICT;

-- Sets and collections shown on the site.

CREATE TABLE material (
  content_id     TEXT PRIMARY KEY,
  data_path      TEXT NOT NULL UNIQUE,
  schema_version TEXT NOT NULL,
  title          TEXT NOT NULL,
  section        TEXT NOT NULL CHECK (section IN ('reading_writing', 'math')),
  -- The source this material reproduces, for placements that name none.
  source_id      TEXT REFERENCES source (source_id)
) STRICT;

-- Key order of one canonical JSON record, so the export reproduces
-- content/canonical/ byte for byte. The explanation order is more than
-- cosmetic: gradingRevision hashes the explanation as serialized, so changing
-- it would change every stored grade revision.
CREATE TABLE legacy_layout (
  layout_id        TEXT PRIMARY KEY,
  record_keys      TEXT NOT NULL CHECK (json_valid(record_keys)),
  explanation_keys TEXT NOT NULL CHECK (json_valid(explanation_keys)),
  UNIQUE (record_keys, explanation_keys)
) STRICT;

CREATE TABLE material_item (
  content_id             TEXT NOT NULL REFERENCES material (content_id),
  position               INTEGER NOT NULL CHECK (position > 0),
  question_id            TEXT NOT NULL REFERENCES question (question_id),
  question_number        INTEGER NOT NULL,
  source_id              TEXT REFERENCES source (source_id),
  source_question_number ANY,
  module_id              TEXT,
  module_position        INTEGER,
  layout_id              TEXT NOT NULL REFERENCES legacy_layout (layout_id),
  PRIMARY KEY (content_id, position),
  UNIQUE (content_id, question_id)
) STRICT;

-- Derived, never stored.

-- Every source a question is known from: imported files, the source named on a
-- placement, or the source its material reproduces.
CREATE VIEW question_occurrence AS
  SELECT question_id, source_id FROM question_source
  UNION
  SELECT i.question_id, coalesce(i.source_id, m.source_id)
    FROM material_item i JOIN material m USING (content_id)
   WHERE coalesce(i.source_id, m.source_id) IS NOT NULL;

-- One line per question: its best tier (1 = real exam), whether that makes it
-- a real-exam or a practice question, and the most recent year it appeared in
-- a source of that tier.
CREATE VIEW question_origin AS
  WITH ranked AS (
    SELECT o.question_id, f.tier, s.year,
           min(f.tier) OVER (PARTITION BY o.question_id) AS best
      FROM question_occurrence o
      JOIN source s USING (source_id)
      JOIN source_family f USING (family_id)
  )
  SELECT r.question_id, r.best AS tier, t.name AS tier_name, t.kind, max(r.year) AS latest_year
    FROM ranked r JOIN source_tier t ON t.tier = r.best
   WHERE r.tier = r.best
   GROUP BY r.question_id;

-- Everything needed to choose questions for practice, one line per question:
-- pick by section, category (domain), skill, real exam or practice, year and
-- difficulty. The site must use published rows only.
CREATE VIEW practice_pool AS
  SELECT q.question_id, q.status, d.section,
         d.domain_id, d.name AS domain, d.name_zh AS domain_zh,
         s.skill_id, s.name AS skill,
         o.kind, o.tier, o.tier_name, o.latest_year,
         q.difficulty, q.difficulty_basis, q.format
    FROM question q
    JOIN subtype t USING (subtype_id)
    JOIN skill s USING (skill_id)
    JOIN domain d USING (domain_id)
    LEFT JOIN question_origin o USING (question_id);
