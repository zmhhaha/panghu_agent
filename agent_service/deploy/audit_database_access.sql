-- Read-only aggregate audit, run separately in each existing database.
-- Does not invoke application functions or print definitions, arguments or data.
BEGIN READ ONLY;
WITH r AS (
  SELECT oid FROM pg_roles WHERE rolname = 'agent_service'
), namespaces AS (
  SELECT n.oid, has_schema_privilege((SELECT oid FROM r), n.oid, 'USAGE') AS usable,
         has_schema_privilege((SELECT oid FROM r), n.oid, 'CREATE') AS creatable
  FROM pg_namespace n
  WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
), relations AS (
  SELECT c.relkind, n.usable,
         CASE WHEN c.relkind IN ('r','p','v','m','f') THEN
           has_table_privilege((SELECT oid FROM r), c.oid,
             'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
           ELSE false END AS table_access,
         CASE WHEN c.relkind = 'S' THEN
           has_sequence_privilege((SELECT oid FROM r), c.oid, 'SELECT,UPDATE,USAGE')
           ELSE false END AS sequence_access
  FROM pg_class c JOIN namespaces n ON n.oid = c.relnamespace
), routines AS (
  SELECT n.usable, p.prosecdef AS security_definer, owner.rolsuper AS owner_superuser,
         owner.oid = (SELECT oid FROM r) AS owned_by_agent,
         l.lanname AS language, l.lanpltrusted AS language_trusted,
         COALESCE(e.extname, '(not extension-owned)') AS extension,
         e.extversion AS extension_version,
         EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
                 WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE') AS public_execute,
         EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
                 WHERE a.grantee = (SELECT oid FROM r)
                   AND a.privilege_type = 'EXECUTE') AS direct_agent_execute,
         has_function_privilege((SELECT oid FROM r), p.oid, 'EXECUTE') AS executable
  FROM pg_proc p
  JOIN namespaces n ON n.oid = p.pronamespace
  JOIN pg_roles owner ON owner.oid = p.proowner
  JOIN pg_language l ON l.oid = p.prolang
  LEFT JOIN pg_depend dep ON dep.classid = 'pg_proc'::regclass
    AND dep.objid = p.oid AND dep.refclassid = 'pg_extension'::regclass
    AND dep.deptype = 'e'
  LEFT JOIN pg_extension e ON e.oid = dep.refobjid
), routine_groups AS (
  SELECT usable, security_definer, owner_superuser, owned_by_agent,
         language, language_trusted, extension, extension_version,
         public_execute, direct_agent_execute, count(*) AS count
  FROM routines WHERE executable
  GROUP BY usable, security_definer, owner_superuser, owned_by_agent,
           language, language_trusted, extension, extension_version,
           public_execute, direct_agent_execute
), relation_groups AS (
  SELECT relkind, usable, count(*) AS count
  FROM relations WHERE table_access OR sequence_access
  GROUP BY relkind, usable
)
SELECT json_build_object(
  'role_exists', EXISTS (SELECT 1 FROM r),
  'schema_create_count', (SELECT count(*) FROM namespaces WHERE creatable),
  'relation_access_groups', (SELECT COALESCE(json_agg(row_to_json(g)), '[]'::json)
                            FROM relation_groups g),
  'routine_execution_groups', (SELECT COALESCE(json_agg(row_to_json(g)), '[]'::json)
                              FROM routine_groups g));
ROLLBACK;
