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
