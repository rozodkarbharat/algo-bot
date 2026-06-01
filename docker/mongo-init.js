// MongoDB initialisation script.
// Runs once when the container is first created.
// Creates the application database and a dedicated app user.

db = db.getSiblingDB(process.env.MONGO_INITDB_DATABASE || "trading_bot");

db.createCollection("__init__");  // ensure DB is created

print("MongoDB initialised: database =", db.getName());
