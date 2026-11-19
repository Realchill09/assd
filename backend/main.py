import json
import uuid
from pathlib import Path
from typing import Any, Generator

import mysql.connector
from dotenv import dotenv_values
from fastapi import Depends, FastAPI, File, HTTPException, Path as ApiPath, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from mysql.connector import MySQLConnection
from mysql.connector.errors import Error as MySQLError
from pydantic import BaseModel, Field

SETTINGS = dotenv_values(Path(__file__).with_name(".env"))


def setting(name: str, default: str) -> str:
    value = SETTINGS.get(name)
    return default if value is None else value


ADMIN_EMAIL = setting("ADMIN_EMAIL", "renz@gmail.com").strip().casefold()
SESSION_AGE = 12 * 60 * 60
SESSION_COOKIE_SECURE = setting("SESSION_COOKIE_SECURE", "false").lower() == "true"
UPLOAD_DIRECTORY = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGES = {
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".webp": ("image/webp", lambda data: data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}

app = FastAPI(title="Food Archive API", version="1.0.0")
allowed_origins = [
    origin.strip()
    for origin in setting(
        "CORS_ORIGINS",
        "http://localhost:4200,http://127.0.0.1:4200,http://localhost:8100,"
        "http://127.0.0.1:8100,capacitor://localhost,http://localhost",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIRECTORY)), name="uploads")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=1, max_length=72)


class ProfileRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: str = Field(min_length=3, max_length=150)


class RecipeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=100)
    description: str = ""
    ingredients: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    image: str | None = Field(default=None, max_length=255)
    cooking_time: int | None = Field(default=None, ge=0)


class RecipeUpdateRequest(RecipeRequest):
    id: int = Field(gt=0)


class RecipePatchRequest(BaseModel):
    id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=150)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    ingredients: list[str] | None = None
    steps: list[str] | None = None
    image: str | None = Field(default=None, max_length=255)
    cooking_time: int | None = Field(default=None, ge=0)


class CommentRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    message: str = Field(min_length=1, max_length=5000)


class CommentUpdateRequest(BaseModel):
    id: int = Field(gt=0)
    message: str = Field(min_length=1, max_length=5000)


class CommentPatchRequest(BaseModel):
    id: int = Field(gt=0)
    message: str | None = Field(default=None, min_length=1, max_length=5000)


class IdRequest(BaseModel):
    recipe_id: int = Field(gt=0)


class RatingRequest(IdRequest):
    rating: int = Field(ge=1, le=5)


class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ImageUploadResponse(BaseModel):
    success: bool
    url: str


def get_db() -> Generator[MySQLConnection, None, None]:
    try:
        db = mysql.connector.connect(
            host=setting("DB_HOST", "127.0.0.1"),
            port=int(setting("DB_PORT", "3306")),
            user=setting("DB_USER", "root"),
            password=setting("DB_PASSWORD", ""),
            database=setting("DB_NAME", "food_archive"),
            connection_timeout=5,
        )
    except MySQLError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to MySQL. Check the backend .env settings.",
        ) from exc

    try:
        yield db
    finally:
        db.close()


def is_admin(user: dict[str, Any]) -> bool:
    return (
        user.get("role") == "admin"
        and str(user.get("email", "")).strip().casefold() == ADMIN_EMAIL
    )


def get_current_user(
    request: Request,
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    session_id = request.cookies.get("foodarchive_session")
    if not session_id:
        raise HTTPException(status_code=401, detail="Login required.")

    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            """
            SELECT u.id, u.username, u.email, u.role
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_id = %s AND s.expires_at > UTC_TIMESTAMP()
            """,
            (session_id,),
        )
        user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="Login session expired.")
    user["role"] = "admin" if is_admin(user) else "user"
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def decode_array(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        value = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [line for line in str(value).splitlines() if line]
    return [str(item) for item in value] if isinstance(value, list) else []


def format_recipe(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "ingredients": decode_array(row.get("ingredients")), "steps": decode_array(row.get("steps"))}


def require_collection_owner(cursor: Any, collection_id: int, user_id: int) -> None:
    cursor.execute(
        "SELECT id FROM collections WHERE id = %s AND user_id = %s",
        (collection_id, user_id),
    )
    if cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Collection not found.")


def rating_summary(db: MySQLConnection, recipe_id: int, user_id: int) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            """
            SELECT ROUND(COALESCE(AVG(rating), 0), 1) AS average_rating,
                   COUNT(*) AS rating_count,
                   MAX(CASE WHEN user_id = %s THEN rating END) AS user_rating
            FROM ratings
            WHERE recipe_id = %s
            """,
            (user_id, recipe_id),
        )
        result = cursor.fetchone()
    return {
        "average_rating": float(result["average_rating"]),
        "rating_count": result["rating_count"],
        "user_rating": result["user_rating"],
    }


@app.get("/api/health")
def health(db: MySQLConnection = Depends(get_db)) -> dict[str, str]:
    with db.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return {"status": "ok", "database": "connected"}


@app.post("/api/auth/register")
def register(request: RegisterRequest, db: MySQLConnection = Depends(get_db)) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT id FROM users WHERE username = %s OR email = %s LIMIT 1",
            (request.username, request.email),
        )
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Username or email is already registered.")
        cursor.execute(
            "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, 'user')",
            (request.username, request.email, request.password),
        )
        db.commit()
    return {"success": True, "message": "Registration successful."}


@app.post("/api/auth/login")
def login(request: LoginRequest, response: Response, db: MySQLConnection = Depends(get_db)) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            """
            SELECT id, username, email, role, password AS stored_password
            FROM users WHERE email = %s LIMIT 1
            """,
            (request.email,),
        )
        user = cursor.fetchone()

    if not user or request.password != user["stored_password"]:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    public_user = {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": "admin" if is_admin(user) else "user",
    }
    session_id = uuid.uuid4().hex
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM user_sessions WHERE expires_at <= UTC_TIMESTAMP()")
        cursor.execute(
            """
            INSERT INTO user_sessions (session_id, user_id, expires_at)
            VALUES (%s, %s, UTC_TIMESTAMP() + INTERVAL 12 HOUR)
            """,
            (session_id, user["id"]),
        )
        db.commit()

    response.set_cookie(
        key="foodarchive_session",
        value=session_id,
        max_age=SESSION_AGE,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )
    return {"success": True, "message": "Login successful.", "user": public_user}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: MySQLConnection = Depends(get_db)) -> dict[str, Any]:
    session_id = request.cookies.get("foodarchive_session")
    if session_id:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM user_sessions WHERE session_id = %s", (session_id,))
            db.commit()
    response.delete_cookie(
        key="foodarchive_session",
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
    )
    return {"success": True, "message": "Logged out."}


@app.get("/api/auth/me")
def current_user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"success": True, "message": "Current user loaded.", "user": user}


@app.put("/api/auth/profile")
def update_profile(
    request: ProfileRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT id FROM users WHERE (username = %s OR email = %s) AND id <> %s LIMIT 1",
            (request.username, request.email, user["id"]),
        )
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="Username or email is already registered.")
        cursor.execute(
            "UPDATE users SET username = %s, email = %s WHERE id = %s",
            (request.username, request.email, user["id"]),
        )
        cursor.execute("SELECT id, username, email, role FROM users WHERE id = %s", (user["id"],))
        updated = cursor.fetchone()
        db.commit()

    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    updated["role"] = "admin" if is_admin(updated) else "user"
    return {"success": True, "message": "Profile updated successfully.", "user": updated}


@app.get("/api/recipes")
def get_recipes(
    recipe_id: int | None = Query(default=None, alias="id", gt=0),
    _: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    columns = "id, name, category, description, ingredients, steps, image, cooking_time, created_by"
    with db.cursor(dictionary=True) as cursor:
        if recipe_id is not None:
            cursor.execute(f"SELECT {columns} FROM recipes WHERE id = %s", (recipe_id,))
            recipe = cursor.fetchone()
            if not recipe:
                raise HTTPException(status_code=404, detail="Recipe not found.")
            return {"success": True, "message": "Recipe loaded.", "recipe": format_recipe(recipe)}
        cursor.execute(f"SELECT {columns} FROM recipes ORDER BY id DESC")
        recipes = [format_recipe(row) for row in cursor.fetchall()]
    return {"success": True, "message": "Recipes loaded.", "recipes": recipes}


@app.post("/api/recipes")
def add_recipe(
    request: RecipeRequest,
    user: dict[str, Any] = Depends(require_admin),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO recipes
            (name, category, description, ingredients, steps, image, cooking_time, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.name, request.category, request.description,
                json.dumps(request.ingredients), json.dumps(request.steps),
                request.image, request.cooking_time, user["id"],
            ),
        )
        recipe_id = cursor.lastrowid
        db.commit()
    recipe = {"id": recipe_id, **request.model_dump(), "created_by": user["id"]}
    return {"success": True, "message": "Recipe added.", "recipe": recipe}


@app.put("/api/recipes")
def update_recipe(
    request: RecipeUpdateRequest,
    _: dict[str, Any] = Depends(require_admin),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE recipes SET name = %s, category = %s, description = %s,
            ingredients = %s, steps = %s, image = %s, cooking_time = %s WHERE id = %s
            """,
            (
                request.name, request.category, request.description,
                json.dumps(request.ingredients), json.dumps(request.steps),
                request.image, request.cooking_time, request.id,
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        db.commit()
    return {"success": True, "message": "Recipe updated.", "recipe": request.model_dump()}


@app.patch("/api/recipes")
def patch_recipe(
    request: RecipePatchRequest,
    _: dict[str, Any] = Depends(require_admin),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    updates = request.model_dump(exclude_unset=True, exclude={"id"})
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one recipe field to update.")

    for field in ("name", "category", "description", "ingredients", "steps"):
        if field in updates and updates[field] is None:
            raise HTTPException(status_code=422, detail="This recipe field cannot be null.")
    if "ingredients" in updates:
        updates["ingredients"] = json.dumps(updates["ingredients"])
    if "steps" in updates:
        updates["steps"] = json.dumps(updates["steps"])

    columns = {
        "name": "name", "category": "category", "description": "description",
        "ingredients": "ingredients", "steps": "steps", "image": "image",
        "cooking_time": "cooking_time",
    }
    assignments = ", ".join(f"{columns[field]} = %s" for field in updates)
    with db.cursor() as cursor:
        cursor.execute(f"UPDATE recipes SET {assignments} WHERE id = %s", (*updates.values(), request.id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        db.commit()
    return {"success": True, "message": "Recipe updated.", "recipe": {"id": request.id, **updates}}


@app.delete("/api/recipes")
def delete_recipe(
    recipe_id: int = Query(alias="id", gt=0),
    _: dict[str, Any] = Depends(require_admin),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute("DELETE FROM recipes WHERE id = %s", (recipe_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        db.commit()
    return {"success": True, "message": "Recipe deleted."}


@app.get("/api/comments")
def get_comments(
    recipe_id: int = Query(gt=0),
    _: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            """
            SELECT c.id, c.recipe_id, c.user_id, u.username, c.message, c.created_at
            FROM comments c JOIN users u ON u.id = c.user_id
            WHERE c.recipe_id = %s ORDER BY c.created_at ASC, c.id ASC
            """,
            (recipe_id,),
        )
        comments = cursor.fetchall()
    return {"success": True, "message": "Comments loaded.", "comments": comments}


@app.post("/api/comments")
def add_comment(
    request: CommentRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM recipes WHERE id = %s", (request.recipe_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        cursor.execute(
            "INSERT INTO comments (recipe_id, user_id, message) VALUES (%s, %s, %s)",
            (request.recipe_id, user["id"], request.message),
        )
        comment_id = cursor.lastrowid
        db.commit()
    return {"success": True, "message": "Comment added.", "comment": {"id": comment_id}}


def update_owned_comment(db: MySQLConnection, user_id: int, comment_id: int, message: str) -> None:
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE comments SET message = %s WHERE id = %s AND user_id = %s",
            (message, comment_id, user_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Comment not found.")
        db.commit()


@app.put("/api/comments")
def update_comment(
    request: CommentUpdateRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    update_owned_comment(db, user["id"], request.id, request.message)
    return {"success": True, "message": "Comment updated."}


@app.patch("/api/comments")
def patch_comment(
    request: CommentPatchRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    if request.message is None:
        raise HTTPException(status_code=422, detail="Provide a comment message to update.")
    update_owned_comment(db, user["id"], request.id, request.message)
    return {"success": True, "message": "Comment updated."}


@app.delete("/api/comments")
def delete_comment(
    comment_id: int = Query(alias="id", gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        if is_admin(user):
            cursor.execute("DELETE FROM comments WHERE id = %s", (comment_id,))
        else:
            cursor.execute(
                "DELETE FROM comments WHERE id = %s AND user_id = %s",
                (comment_id, user["id"]),
            )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Comment not found.")
        db.commit()
    return {"success": True, "message": "Comment deleted."}


@app.get("/api/favorites")
def get_favorites(
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT recipe_id FROM favorites WHERE user_id = %s ORDER BY recipe_id",
            (user["id"],),
        )
        favorite_ids = [row[0] for row in cursor.fetchall()]
    return {"success": True, "favorites": favorite_ids}


@app.post("/api/favorites")
def add_favorite(
    request: IdRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM recipes WHERE id = %s", (request.recipe_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        try:
            cursor.execute(
                "INSERT INTO favorites (user_id, recipe_id) VALUES (%s, %s)",
                (user["id"], request.recipe_id),
            )
        except mysql.connector.IntegrityError as exc:
            if exc.errno != 1062:
                raise
        db.commit()
    return {"success": True, "message": "Recipe added to favorites."}


@app.delete("/api/favorites")
def remove_favorite(
    recipe_id: int = Query(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND recipe_id = %s",
            (user["id"], recipe_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Favorite not found.")
        db.commit()
    return {"success": True, "message": "Recipe removed from favorites."}


@app.get("/api/ratings")
def get_rating(
    recipe_id: int = Query(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM recipes WHERE id = %s", (recipe_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Recipe not found.")
    return {"success": True, "rating": rating_summary(db, recipe_id, user["id"])}


@app.put("/api/ratings")
def save_rating(
    request: RatingRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM recipes WHERE id = %s", (request.recipe_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        cursor.execute(
            """
            INSERT INTO ratings (user_id, recipe_id, rating) VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE rating = VALUES(rating)
            """,
            (user["id"], request.recipe_id, request.rating),
        )
        db.commit()
    return {
        "success": True,
        "message": "Rating saved.",
        "rating": rating_summary(db, request.recipe_id, user["id"]),
    }


@app.get("/api/collections")
def get_collections(
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT id, name FROM collections WHERE user_id = %s ORDER BY name",
            (user["id"],),
        )
        collections = cursor.fetchall()
        for collection in collections:
            cursor.execute(
                """
                SELECT r.id, r.name, r.category, r.description, r.ingredients, r.steps,
                       r.image, r.cooking_time, r.created_by
                FROM collection_recipes cr
                JOIN recipes r ON r.id = cr.recipe_id
                WHERE cr.collection_id = %s
                ORDER BY r.name
                """,
                (collection["id"],),
            )
            collection["recipes"] = [format_recipe(row) for row in cursor.fetchall()]
    return {"success": True, "collections": collections}


@app.post("/api/collections")
def add_collection(
    request: CollectionRequest,
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Collection name cannot be blank.")
    with db.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO collections (user_id, name) VALUES (%s, %s)",
                (user["id"], name),
            )
        except mysql.connector.IntegrityError as exc:
            if exc.errno == 1062:
                raise HTTPException(status_code=409, detail="A collection with this name already exists.") from exc
            raise
        collection_id = cursor.lastrowid
        db.commit()
    return {"success": True, "message": "Collection created.", "collection": {"id": collection_id, "name": name}}


@app.patch("/api/collections/{collection_id}")
def rename_collection(
    request: CollectionRequest,
    collection_id: int = ApiPath(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Collection name cannot be blank.")
    with db.cursor() as cursor:
        require_collection_owner(cursor, collection_id, user["id"])
        try:
            cursor.execute(
                "UPDATE collections SET name = %s WHERE id = %s AND user_id = %s",
                (name, collection_id, user["id"]),
            )
        except mysql.connector.IntegrityError as exc:
            if exc.errno == 1062:
                raise HTTPException(status_code=409, detail="A collection with this name already exists.") from exc
            raise
        db.commit()
    return {"success": True, "message": "Collection renamed."}


@app.delete("/api/collections/{collection_id}")
def delete_collection(
    collection_id: int = ApiPath(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        require_collection_owner(cursor, collection_id, user["id"])
        cursor.execute("DELETE FROM collection_recipes WHERE collection_id = %s", (collection_id,))
        cursor.execute("DELETE FROM collections WHERE id = %s AND user_id = %s", (collection_id, user["id"]))
        db.commit()
    return {"success": True, "message": "Collection deleted."}


@app.get("/api/collections/{collection_id}/recipes")
def get_collection_recipes(
    collection_id: int = ApiPath(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor(dictionary=True) as cursor:
        require_collection_owner(cursor, collection_id, user["id"])
        cursor.execute(
            """
            SELECT r.id, r.name, r.category, r.description, r.ingredients, r.steps,
                   r.image, r.cooking_time, r.created_by
            FROM collection_recipes cr
            JOIN recipes r ON r.id = cr.recipe_id
            WHERE cr.collection_id = %s
            ORDER BY r.name
            """,
            (collection_id,),
        )
        recipes = [format_recipe(row) for row in cursor.fetchall()]
    return {"success": True, "recipes": recipes}


@app.post("/api/collections/{collection_id}/recipes")
def add_recipe_to_collection(
    request: IdRequest,
    collection_id: int = ApiPath(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        require_collection_owner(cursor, collection_id, user["id"])
        cursor.execute("SELECT id FROM recipes WHERE id = %s", (request.recipe_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        try:
            cursor.execute(
                "INSERT INTO collection_recipes (collection_id, recipe_id) VALUES (%s, %s)",
                (collection_id, request.recipe_id),
            )
        except mysql.connector.IntegrityError as exc:
            if exc.errno != 1062:
                raise
        db.commit()
    return {"success": True, "message": "Recipe added to collection."}


@app.delete("/api/collections/{collection_id}/recipes/{recipe_id}")
def remove_recipe_from_collection(
    collection_id: int = ApiPath(gt=0),
    recipe_id: int = ApiPath(gt=0),
    user: dict[str, Any] = Depends(get_current_user),
    db: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    with db.cursor() as cursor:
        require_collection_owner(cursor, collection_id, user["id"])
        cursor.execute(
            "DELETE FROM collection_recipes WHERE collection_id = %s AND recipe_id = %s",
            (collection_id, recipe_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Recipe is not in this collection.")
        db.commit()
    return {"success": True, "message": "Recipe removed from collection."}


@app.post("/api/uploads", response_model=ImageUploadResponse)
def upload_recipe_image(
    request: Request,
    image: UploadFile = File(...),
    _: dict[str, Any] = Depends(require_admin),
) -> ImageUploadResponse:
    extension = Path(image.filename or "").suffix.lower()
    image_type = ALLOWED_IMAGES.get(extension)
    if image_type is None or image.content_type != image_type[0]:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, or WebP image.")

    content = image.file.read(UPLOAD_MAX_BYTES + 1)
    if len(content) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 5 MB or smaller.")
    if not content or not image_type[1](content):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid image.")

    filename = f"{uuid.uuid4().hex}{extension}"
    (UPLOAD_DIRECTORY / filename).write_bytes(content)
    image.file.close()
    url = f"{str(request.base_url).rstrip('/')}/api/uploads/{filename}"
    return ImageUploadResponse(success=True, url=url)