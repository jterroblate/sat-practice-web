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

-- What a question asks for within its skill: "main purpose" against "overall
-- structure", a contrast transition against a causal one. This is the
-- classification the site shows. It is kept apart from subtype, whose rows
-- repeat the wording of the set files and cannot be renamed without changing
-- them. A task belongs to one skill; scripts/classify_questions.py reads the
-- task off the question, and for a handful of questions that evidence points
-- to another skill than the recorded one (view question_skill_conflict).
CREATE TABLE task (
  task_id  TEXT PRIMARY KEY,
  skill_id TEXT NOT NULL REFERENCES skill (skill_id),
  name_zh  TEXT NOT NULL,
  name     TEXT NOT NULL,
  position INTEGER NOT NULL CHECK (position > 0),
  UNIQUE (skill_id, name_zh),
  UNIQUE (skill_id, position)
) STRICT;

-- The kind of text a Reading and Writing question is about. 'literature' is
-- an excerpt of a literary work or a question about one; 'information' is
-- everything else, by subject.
CREATE TABLE passage_type (
  passage_type TEXT PRIMARY KEY,
  family       TEXT NOT NULL CHECK (family IN ('literature', 'information')),
  name_zh      TEXT NOT NULL UNIQUE,
  position     INTEGER NOT NULL UNIQUE CHECK (position > 0)
) STRICT;

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
-- passage_type, task_id: set by scripts/classify_questions.py for every
--              Reading and Writing question; both NULL for Math.
-- passage_type_basis: 'source_intro' when the passage names its own kind,
--              'word_list' when its vocabulary clearly belongs to one subject,
--              'reviewed' when a reader decided.
-- task_basis:  'rule' when read off the stem and the choices, 'reviewed' when
--              a reader decided.

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
  passage_type        TEXT REFERENCES passage_type (passage_type),
  passage_type_basis  TEXT CHECK (passage_type_basis IN ('source_intro', 'word_list', 'reviewed')),
  task_id             TEXT REFERENCES task (task_id),
  task_basis          TEXT CHECK (task_basis IN ('rule', 'reviewed')),
  CHECK ((format = 'choice') = (correct_answer IS NOT NULL)),
  CHECK ((difficulty IS NULL) = (difficulty_basis IS NULL)),
  CHECK ((passage_type IS NULL) = (passage_type_basis IS NULL)),
  CHECK ((task_id IS NULL) = (task_basis IS NULL)),
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

-- Hard words explained for learners. One row is one word in one sense: a plain
-- English definition and a Chinese one. basis says who vouches for the wording
-- of the row and of its example sentences: 'ai_draft' was written by an AI
-- model and not checked by a person. Nothing here is copied from a dictionary.
CREATE TABLE vocabulary (
  vocab_id       TEXT PRIMARY KEY CHECK (vocab_id GLOB '[a-z]*' AND vocab_id NOT GLOB '*[^a-z0-9-]*'),
  headword       TEXT NOT NULL CHECK (headword <> ''),
  part_of_speech TEXT NOT NULL CHECK (part_of_speech <> ''),
  definition_en  TEXT NOT NULL CHECK (definition_en <> ''),
  definition_zh  TEXT NOT NULL CHECK (definition_zh <> ''),
  basis          TEXT NOT NULL CHECK (basis IN ('ai_draft', 'human_checked')),
  UNIQUE (headword, definition_en)
) STRICT;

-- Sentences that show an explained word in use, each with its translation,
-- in the order the learner reads them. None is taken from a question. Every
-- explanation has at least three (scripts/content_db.py check).
CREATE TABLE vocabulary_example (
  vocab_id   TEXT NOT NULL REFERENCES vocabulary (vocab_id),
  position   INTEGER NOT NULL CHECK (position > 0),
  example_en TEXT NOT NULL CHECK (example_en <> ''),
  example_zh TEXT NOT NULL CHECK (example_zh <> ''),
  PRIMARY KEY (vocab_id, position),
  UNIQUE (vocab_id, example_en)
) STRICT;

-- The spellings an explanation is found by, in lower case: the word and its
-- inflected forms. A spelling belongs to one explanation; a question whose
-- text uses it in another sense says so in question_vocabulary.
CREATE TABLE vocabulary_form (
  form     TEXT PRIMARY KEY CHECK (form <> '' AND form = lower(form)),
  vocab_id TEXT NOT NULL REFERENCES vocabulary (vocab_id)
) STRICT;

-- Which explained words a question contains. surface is the word exactly as
-- it stands in the passage, the prompt or the choices. reveal says when the
-- learner may open the explanation: the answer choices, and the word a
-- "most nearly mean" question asks about, only after the answer is submitted.
CREATE TABLE question_vocabulary (
  question_id TEXT NOT NULL REFERENCES question (question_id),
  field       TEXT NOT NULL CHECK (field IN ('passage', 'prompt', 'choices')),
  surface     TEXT NOT NULL CHECK (surface <> ''),
  vocab_id    TEXT NOT NULL REFERENCES vocabulary (vocab_id),
  reveal      TEXT NOT NULL CHECK (reveal IN ('always', 'after_submit')),
  PRIMARY KEY (question_id, field, surface),
  CHECK (field <> 'choices' OR reveal = 'after_submit')
) STRICT;

-- A question read through for a learner, in English and in Chinese, shown
-- only after the answer is submitted. One row here says how the answer is
-- reached; walkthrough_segment reads the passage and the prompt a part at a
-- time, walkthrough_choice takes every answer choice in turn. basis says who
-- vouches for all three, as in vocabulary. A question either has none of
-- this or all of it: at least one segment and a note on each of its choices
-- (scripts/content_db.py check).
CREATE TABLE walkthrough (
  question_id TEXT PRIMARY KEY REFERENCES question (question_id),
  approach_en TEXT NOT NULL CHECK (approach_en <> ''),
  approach_zh TEXT NOT NULL CHECK (approach_zh <> ''),
  basis       TEXT NOT NULL CHECK (basis IN ('ai_draft', 'human_checked'))
) STRICT;

-- One part of the passage or of the prompt, in reading order. excerpt is that
-- part as the learner reads it: plain text, without the inline markers. It
-- must stand in the field it names, after the excerpt before it, so a segment
-- cannot outlive a change to the text it explains.
CREATE TABLE walkthrough_segment (
  question_id TEXT NOT NULL REFERENCES walkthrough (question_id),
  position    INTEGER NOT NULL CHECK (position > 0),
  field       TEXT NOT NULL CHECK (field IN ('passage', 'prompt')),
  excerpt     TEXT NOT NULL CHECK (excerpt <> ''),
  note_en     TEXT NOT NULL CHECK (note_en <> ''),
  note_zh     TEXT NOT NULL CHECK (note_zh <> ''),
  PRIMARY KEY (question_id, position)
) STRICT;

-- Why one answer choice is right or wrong, and what its words mean.
CREATE TABLE walkthrough_choice (
  question_id TEXT NOT NULL REFERENCES walkthrough (question_id),
  label       TEXT NOT NULL,
  note_en     TEXT NOT NULL CHECK (note_en <> ''),
  note_zh     TEXT NOT NULL CHECK (note_zh <> ''),
  PRIMARY KEY (question_id, label),
  FOREIGN KEY (question_id, label) REFERENCES choice (question_id, label)
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

-- How much explanation a question has, one line per question:
--   'full'  every explanation text is written and every choice is analysed,
--           or the question has a walkthrough;
--   'brief' something is written, but not all of it (usually one or two
--           sentences on the correct answer only);
--   'none'  nothing is written.
-- It is read off the explanation, choice and walkthrough rows, never stored,
-- so it cannot disagree with them.
CREATE VIEW question_explanation AS
  WITH written AS (
    SELECT q.question_id,
           (coalesce(trim(e.short), '') <> '') + (coalesce(trim(e.key_evidence), '') <> '')
             + (coalesce(trim(e.why_correct), '') <> '') + (coalesce(trim(e.detailed), '') <> '') AS texts,
           (SELECT count(*) FROM choice c WHERE c.question_id = q.question_id) AS choices,
           (SELECT count(*) FROM choice c
             WHERE c.question_id = q.question_id AND coalesce(trim(c.analysis), '') <> '') AS analysed,
           EXISTS (SELECT 1 FROM walkthrough w WHERE w.question_id = q.question_id) AS walked
      FROM question q LEFT JOIN explanation e USING (question_id)
  )
  SELECT question_id,
         CASE WHEN walked OR (texts = 4 AND analysed = choices) THEN 'full'
              WHEN texts = 0 AND analysed = 0 THEN 'none'
              ELSE 'brief' END AS level
    FROM written;

-- How much a question matters for practice, one line per question, read off
-- where it has appeared and never stored:
--   3 'core'       a real-exam question that came up at two or more sittings,
--                  or at a sitting of the most recent exam year in the bank;
--   2 'important'  any other real-exam question;
--   1 'basic'      practice material: question bank, practice tests, or no
--                  known source.
-- sittings counts distinct exam dates (year and month), so the versions of
-- one sitting count once. A year that is unknown counts as not recent.
CREATE VIEW question_importance AS
  WITH seen AS (
    SELECT o.question_id, f.tier, s.year, s.month
      FROM question_occurrence o
      JOIN source s USING (source_id)
      JOIN source_family f USING (family_id)
  ), exam AS (
    SELECT question_id,
           count(DISTINCT CASE WHEN year IS NOT NULL THEN year * 100 + coalesce(month, 0) END) AS sittings,
           max(year) AS latest_year
      FROM seen WHERE tier = 1 GROUP BY question_id
  ), recent AS (
    SELECT max(s.year) AS year FROM source s JOIN source_family f USING (family_id) WHERE f.tier = 1
  )
  SELECT q.question_id,
         CASE WHEN e.question_id IS NULL THEN 1
              WHEN e.sittings >= 2 OR e.latest_year = (SELECT year FROM recent) THEN 3
              ELSE 2 END AS level,
         CASE WHEN e.question_id IS NULL THEN 'basic'
              WHEN e.sittings >= 2 OR e.latest_year = (SELECT year FROM recent) THEN 'core'
              ELSE 'important' END AS importance,
         coalesce(e.sittings, 0) AS sittings, e.latest_year
    FROM question q LEFT JOIN exam e USING (question_id);

-- How often each task comes up in real exams: its share of all classified
-- real-exam questions, one line per task. A question that appears in several
-- versions of a sitting counts once.
CREATE VIEW task_frequency AS
  WITH real AS (
    SELECT q.question_id, q.task_id
      FROM question q JOIN question_origin o USING (question_id)
     WHERE o.tier = 1 AND q.task_id IS NOT NULL
  )
  SELECT k.task_id, k.skill_id, k.name_zh, count(r.question_id) AS questions,
         round(100.0 * count(r.question_id) / (SELECT count(*) FROM real), 2) AS percent
    FROM task k LEFT JOIN real r USING (task_id)
   GROUP BY k.task_id;

-- Questions whose task belongs to another skill than the one recorded for
-- them. The recorded skill comes from the set files and is left as it is;
-- practice_pool follows the task.
CREATE VIEW question_skill_conflict AS
  SELECT q.question_id, q.status, q.skill_basis, recorded.name AS recorded_skill, clean.name AS task_skill, q.task_id
    FROM question q
    JOIN subtype t USING (subtype_id)
    JOIN skill recorded ON recorded.skill_id = t.skill_id
    JOIN task k USING (task_id)
    JOIN skill clean ON clean.skill_id = k.skill_id
   WHERE clean.skill_id <> recorded.skill_id;

-- Everything needed to choose questions for practice, one line per question:
-- pick by section, category (domain), skill, task, kind of passage, real exam
-- or practice, year, difficulty, importance and how much explanation there is.
-- Skill and domain follow the task where there is one, the recorded
-- classification otherwise (Math). The site must use published rows only.
CREATE VIEW practice_pool AS
  SELECT q.question_id, q.status, d.section,
         d.domain_id, d.name AS domain, d.name_zh AS domain_zh,
         s.skill_id, s.name AS skill,
         q.task_id, k.name_zh AS task_zh,
         q.passage_type, p.name_zh AS passage_type_zh, p.family AS passage_family,
         o.kind, o.tier, o.tier_name, o.latest_year,
         q.difficulty, q.difficulty_basis, q.format,
         i.importance, i.level AS importance_level, i.sittings,
         x.level AS explanation
    FROM question q
    JOIN subtype t USING (subtype_id)
    LEFT JOIN task k USING (task_id)
    JOIN skill s ON s.skill_id = coalesce(k.skill_id, t.skill_id)
    JOIN domain d USING (domain_id)
    LEFT JOIN passage_type p USING (passage_type)
    JOIN question_explanation x USING (question_id)
    JOIN question_importance i USING (question_id)
    LEFT JOIN question_origin o USING (question_id);

-- How far the word explanations reach: published Reading and Writing questions
-- by importance, and how many of them have at least one explained word.
CREATE VIEW vocabulary_coverage AS
  SELECT p.importance, p.importance_level AS level, count(*) AS questions,
         sum(EXISTS (SELECT 1 FROM question_vocabulary v WHERE v.question_id = p.question_id)) AS annotated
    FROM practice_pool p
   WHERE p.status = 'published' AND p.section = 'reading_writing'
   GROUP BY p.importance, p.importance_level;

-- One line per explained word, for looking after the list: who vouches for
-- it, how many spellings it is found by, how many example sentences it has,
-- and in how many questions it is linked (and in how many of those it opens
-- only after the answer is in).
CREATE VIEW vocabulary_use AS
  SELECT v.vocab_id, v.headword, v.part_of_speech, v.basis,
         (SELECT count(*) FROM vocabulary_form f WHERE f.vocab_id = v.vocab_id) AS spellings,
         (SELECT count(*) FROM vocabulary_example x WHERE x.vocab_id = v.vocab_id) AS examples,
         count(DISTINCT l.question_id) AS questions,
         count(DISTINCT CASE WHEN l.reveal = 'after_submit' THEN l.question_id END) AS after_submit_questions
    FROM vocabulary v
    LEFT JOIN question_vocabulary l USING (vocab_id)
   GROUP BY v.vocab_id;

-- How far the walkthroughs reach: published Reading and Writing questions by
-- importance, and how many of them have one.
CREATE VIEW walkthrough_coverage AS
  SELECT p.importance, p.importance_level AS level, count(*) AS questions,
         sum(EXISTS (SELECT 1 FROM walkthrough w WHERE w.question_id = p.question_id)) AS walked
    FROM practice_pool p
   WHERE p.status = 'published' AND p.section = 'reading_writing'
   GROUP BY p.importance, p.importance_level;
