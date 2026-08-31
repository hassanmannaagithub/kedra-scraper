// Transform is read-only on landing_zone so source data remains immutable.
// Sources/sections config lives in config/sources.yaml now, not Mongo, so
// no user needs a role on a "config" database.
db = db.getSiblingDB("admin");

db.createUser({
  user: "spider",
  pwd: "spider",
  roles: [
    { role: "readWrite", db: "landing_zone" },
    { role: "readWrite", db: "meta" },
  ],
});

db.createUser({
  user: "transform",
  pwd: "transform",
  roles: [
    { role: "read", db: "landing_zone" },
    { role: "readWrite", db: "transformed" },
    { role: "readWrite", db: "meta" },
  ],
});

db.createUser({
  user: "ingest_admin",
  pwd: "ingest_admin",
  roles: [
    { role: "readWrite", db: "landing_zone" },
    { role: "readWrite", db: "transformed" },
    { role: "readWrite", db: "meta" },
  ],
});
