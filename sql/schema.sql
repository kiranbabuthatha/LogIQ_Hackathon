-- LogIQ schema: raw Loghub tables -> one unified `events` table -> full-text index.
-- Run with `python load.py` (it substitutes {BGL_SOURCE} and {OPENSTACK_SOURCE}).
-- Safe to re-run: everything is dropped and rebuilt.

DROP VIEW IF EXISTS events_v;
DROP VIEW IF EXISTS component_team;
DROP TABLE IF EXISTS events;
DROP TEXT SEARCH DICTIONARY IF EXISTS log_text;
DROP TABLE IF EXISTS ownership;
DROP TABLE IF EXISTS bgl;
DROP TABLE IF EXISTS openstack;

-- 1. Raw structured logs, read straight from CSV (HTTPS URL or a path the server can see).
CREATE TABLE bgl AS
SELECT * FROM read_csv('{BGL_SOURCE}', header = true);

CREATE TABLE openstack AS
SELECT * FROM read_csv('{OPENSTACK_SOURCE}', header = true);

-- 2. Team ownership: which team owns a component, matched by longest prefix.
-- SAMPLE DATA: replace these rows with the organisers' team-ownership table.
CREATE TABLE ownership (
    dataset          VARCHAR,
    component_prefix VARCHAR,
    team             VARCHAR
);

INSERT INTO ownership VALUES
    ('bgl',       'KERNEL',            'compute-kernel'),
    ('bgl',       'APP',               'job-runtime'),
    ('bgl',       'MMCS',              'control-system'),
    ('bgl',       'BGLMASTER',         'control-system'),
    ('bgl',       'CMCS',              'control-system'),
    ('bgl',       'DISCOVERY',         'hardware-ops'),
    ('bgl',       'HARDWARE',          'hardware-ops'),
    ('bgl',       'MONITOR',           'hardware-ops'),
    ('bgl',       'LINKCARD',          'interconnect'),
    ('bgl',       'SERV_NET',          'interconnect'),
    ('openstack', 'nova.osapi_compute','compute-api'),
    ('openstack', 'nova.metadata',     'compute-api'),
    ('openstack', 'nova.api',          'compute-api'),
    ('openstack', 'nova.compute',      'compute-core'),
    ('openstack', 'nova.virt',         'virtualization'),
    ('openstack', 'nova.scheduler',    'scheduler');

-- 3. One table for both datasets, with a shared vocabulary.
--    severity: 3 = FATAL/SEVERE/FAILURE/CRITICAL, 2 = ERROR, 1 = WARNING, 0 = everything else.
--    is_alert: BGL's expert alert label (Label <> '-'); NULL where a dataset has no labels.
CREATE TABLE events AS
SELECT 'bgl'                                           AS dataset,
       "LineId"                                        AS line_id,
       strptime("Time", '%Y-%m-%d-%H.%M.%S.%f')        AS ts,
       "Node"                                          AS source,
       "Component"                                     AS component,
       "Level"                                         AS level,
       "Label" <> '-'                                  AS is_alert,
       CASE WHEN "Label" <> '-' THEN "Label" END       AS alert_type,
       "EventId"                                       AS template_id,
       "EventTemplate"                                 AS template,
       "Content"                                       AS content
FROM bgl
UNION ALL
SELECT 'openstack', "LineId", "Date" + "Time", "Logrecord", "Component", "Level",
       NULL, NULL, "EventId", "EventTemplate", "Content"
FROM openstack;

ALTER TABLE events ADD COLUMN severity INTEGER;
UPDATE events SET severity = CASE
    WHEN upper(level) IN ('FATAL', 'SEVERE', 'FAILURE', 'CRITICAL') THEN 3
    WHEN upper(level) = 'ERROR'                                    THEN 2
    WHEN upper(level) IN ('WARNING', 'WARN')                       THEN 1
    ELSE 0 END;

-- 4. Component -> team (longest matching prefix wins; no match = 'unassigned').
CREATE VIEW component_team AS
SELECT dataset, component, coalesce(team, 'unassigned') AS team
FROM (
    SELECT c.dataset, c.component, o.team,
           row_number() OVER (PARTITION BY c.dataset, c.component
                              ORDER BY length(o.component_prefix) DESC NULLS LAST) AS rn
    FROM (SELECT DISTINCT dataset, component FROM events) c
    LEFT JOIN ownership o
           ON o.dataset = c.dataset AND starts_with(c.component, o.component_prefix)
) ranked
WHERE rn = 1;

-- Events with their owning team; a "problem" is severity >= 1 or an expert alert.
CREATE VIEW events_v AS
SELECT e.*, ct.team,
       (e.severity >= 1 OR coalesce(e.is_alert, false)) AS is_problem
FROM events e
JOIN component_team ct USING (dataset, component);

-- 5. Full-text index over the log text (BM25-ranked search via events_idx).
CREATE TEXT SEARCH DICTIONARY log_text AS
    split_text(case := 'lower')
    | normalize_tokens('en_US.UTF-8', accent := false)
    | stem_words('en_US.UTF-8')
    WITH (frequency, position, offset);

CREATE INDEX events_idx ON events
    USING inverted (dataset, line_id, level, component, content log_text);

VACUUM (REFRESH_TABLE) events;
