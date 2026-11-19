# Food Archive Python API

This FastAPI service connects the Angular frontend to the existing MySQL
database. It does not create, drop, or migrate tables.

## Database columns expected by the current API

The existing database should have these columns:

- `users`: `id`, `username`, `email`, `password`, `role`
- `recipes`: `id`, `name`, `category`, `description`, `ingredients`, `steps`,
  `image`, `cooking_time`, `created_by`
- `comments`: `id`, `recipe_id`, `user_id`, `message`, `created_at`
- `user_sessions`: `session_id`, `user_id`, `expires_at`
- `favorites`: `user_id`, `recipe_id`
- `ratings`: `user_id`, `recipe_id`, `rating`
- `collections`: `id`, `user_id`, `name`, `created_at`
- `collection_recipes`: `collection_id`, `recipe_id`

Create the session, favorites, ratings, and collections tables once in
phpMyAdmin or your MySQL client:

```sql
CREATE TABLE user_sessions (
    session_id VARCHAR(128) NOT NULL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    expires_at DATETIME NOT NULL,
    INDEX (expires_at)
);

CREATE TABLE favorites (
    user_id BIGINT NOT NULL,
    recipe_id BIGINT NOT NULL,
    PRIMARY KEY (user_id, recipe_id)
);

CREATE TABLE ratings (
    user_id BIGINT NOT NULL,
    recipe_id BIGINT NOT NULL,
    rating TINYINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    PRIMARY KEY (user_id, recipe_id)
);

CREATE TABLE collections (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_user_collection_name (user_id, name)
);

CREATE TABLE collection_recipes (
    collection_id BIGINT NOT NULL,
    recipe_id BIGINT NOT NULL,
    PRIMARY KEY (collection_id, recipe_id)
);
```

Session tokens are stored directly in `user_sessions.session_id`, without
hashing. If your existing table uses `session_id_hash`, update it once in
MySQL before starting the API:

```sql
ALTER TABLE user_sessions
    CHANGE COLUMN session_id_hash session_id VARCHAR(128) NOT NULL;
```

Existing sessions contain hashes and will no longer match; users must log in
again. Storing session tokens directly means anyone who can read the database
can use those tokens, so keep this setup local and do not expose the database.

`ingredients` and `steps` may be MySQL JSON or text columns containing JSON.
For this local development project, passwords are stored as plain text in
`users.password`, exactly as entered. This is not safe for real users or
deployment: use only unique test passwords and never expose or publish this
database. Public registration still does not grant administrator access. Set `ADMIN_EMAIL` near the top of `main.py` to the email of the one account
that should be an administrator. Register that account normally, then run this
once in your MySQL client to promote it and demote any other administrators:

```sql
UPDATE users
SET role = CASE
    WHEN LOWER(email) = LOWER('renz@gmail.com') THEN 'admin'
    ELSE 'user'
END;
```

Replace the email in the SQL if `ADMIN_EMAIL` is different. The backend only
authorizes that configured email for administrator operations, even if another
database account has its role set to `admin`. Users can update their username
and email from the profile page. Registration passwords must be at least 8
characters.
Accounts created before this change have bcrypt hashes, which cannot be
converted back to their original passwords. Set a new local test password
directly in MySQL or recreate those accounts before logging in.

If your database has different columns, inspect them with your MySQL client and
adapt the SQL in `main.py` to the actual schema before using the endpoints.

Recipes and comments support `GET` to read, `POST` to create, `PUT` for full
updates, `PATCH` for partial updates, and `DELETE` to remove records.
Admins can upload JPEG, PNG, or WebP recipe images up to 5 MB; uploaded files
are served from the backend's `uploads` directory. When editing a recipe,
choose an image file; the returned image URL is filled into the recipe form.
Save the recipe to store that URL in MySQL.

## Run locally with XAMPP

1. Start MySQL in the XAMPP Control Panel.
2. Edit the configuration constants near the top of `main.py` for your MySQL
   host, port, username, password, and database name. `DB_NAME` should be
   `food_archive`; the `C:\xampp\mysql\data\food_archive` path is MySQL's data
   directory, not the connection address. Configure `ADMIN_EMAIL`,
   `SESSION_COOKIE_SECURE`, and `CORS_ORIGINS` there as needed. Set
   `SESSION_COOKIE_SECURE = True` when serving the API over HTTPS.

3. The API keeps login state in a server-side MySQL session for 12 hours and
   sends the browser an HTTP-only cookie.

4. Install dependencies and start the API:

   ```powershell
   .\Scripts\python.exe -m pip install -r requirements.txt
   .\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```

5. Open `http://127.0.0.1:8000/docs` to inspect the routes and
   `http://127.0.0.1:8000/api/health` to test the MySQL connection.

Start the Ionic app separately from the `frontend` directory with
`npx.cmd ionic serve` in PowerShell (`npx ionic serve` in Command Prompt).
By default, Ionic serves the app at `http://localhost:8100`.
After registering the configured admin email, grant it administrator access
and demote any other admins in phpMyAdmin using the SQL above.

The frontend connects to the API at `http://localhost:8000/api` in development.
`CORS_ORIGINS` in `main.py` allows the default Ionic development port, 8100.
Production uses `/api`, which requires the web server to proxy that path to
this FastAPI service.
