from flask import Flask, render_template, request, redirect, session
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = "secret123"

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

# MongoDB connection
client = MongoClient("mongodb://localhost:27017/")
db = client["local_service_vendor"]

# ---------------- HOME ----------------
@app.route("/")
def home():
    return redirect("/login")

# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        data = {
            "name": request.form["name"],
            "email": request.form["email"],
            "password": request.form["password"],
            "phone": request.form["phone"],
            "location": request.form["location"],
            "role": request.form["role"]
        }

        if data["role"] == "provider":
            data["category"] = request.form["category"]
            data["verified"] = False
            data["availability_status"] = True

        db.users.insert_one(data)
        return redirect("/login")

    return render_template("register.html")

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        query = {
            "email": request.form["email"],
            "password": request.form["password"]
        }
        if role:
            query["role"] = role

        user = db.users.find_one(query)

        if user:
            session["user_id"] = str(user["_id"])
            session["role"] = user["role"]

            if user["role"] == "user":
                return redirect("/user_dash")
            elif user["role"] == "provider":
                return redirect("/provider_dash")
            elif user["role"] == "admin":
                return redirect("/admin_dash")

        return "Invalid Login"

    return render_template("login.html")

# ---------------- USER DASHBOARD ----------------
@app.route("/user_dash")
def user_dash():
    # require login
    if "user_id" not in session:
        return redirect("/login")

    # completed bookings available for review
    completed_bookings = list(db.bookings.find({
        "user_id": session["user_id"],
        "status": "Accepted",
        "completed": True
    }))
    for b in completed_bookings:
        existing = db.ratings.find_one({"user_id": session["user_id"], "booking_id": str(b["_id"])})
        b["has_review"] = existing is not None

    categories = list(db.categories.find())
    return render_template("user_dash.html", categories=categories, completed_bookings=completed_bookings)

# ---------------- PROVIDER DASHBOARD ----------------
@app.route("/provider_dash")
@login_required
def provider_dash():
    bookings = []
    for b in db.bookings.find({"provider_id": session["user_id"]}):
        user = db.users.find_one({"_id": ObjectId(b["user_id"])})
        b["user_name"] = user.get("name") if user else ""
        bookings.append(b)
    categories = list(db.categories.find())
    return render_template("provider_dash.html", bookings=bookings, categories=categories)

# ---------------- ADMIN DASHBOARD ----------------
@app.route("/admin_dash")
@login_required
def admin_dash():
    # show unverified providers and existing categories
    providers = list(db.users.find({"role": "provider", "verified": False}))
    categories = list(db.categories.find())
    return render_template("admin_dash.html", providers=providers, categories=categories)

@app.route("/verify/<id>")
def verify(id):
    db.users.update_one({"_id": ObjectId(id)}, {"$set": {"verified": True}})
    return redirect("/admin_dash")


# ---------------- ADMIN CATEGORIES ----------------
@app.route("/admin/categories", methods=["POST"])
@login_required
def add_category():
    # only admin role can add categories
    if session.get("role") != "admin":
        return redirect("/login")

    name = request.form.get("name", "").strip()
    if name:
        # avoid duplicates (case-insensitive)
        exists = db.categories.find_one({"name": {"$regex": f"^{name}$", "$options": "i"}})
        if not exists:
            db.categories.insert_one({"name": name})
    return redirect("/admin_dash")

# ---------------- SEARCH ----------------
@app.route("/search", methods=["GET", "POST"])
def search():
    providers = []
    message = None
    # support POST form submit and GET query params
    location = request.values.get("location", "")
    category = request.values.get("category", "")

    # build query dynamically based on provided fields
    query = {"role": "provider"}
    if location:
        query["location"] = {"$regex": f"{location}", "$options": "i"}
    if category:
        query["category"] = {"$regex": f"{category}", "$options": "i"}

    service = request.values.get("service", "").strip()
    if service:
        # match provider name OR category by service keyword
        query["$or"] = [
            {"name": {"$regex": f"{service}", "$options": "i"}},
            {"category": {"$regex": f"{service}", "$options": "i"}}
        ]

    providers = list(db.users.find(query))
    if not providers:
        message = "No providers found matching your criteria"
    else:
        # calculate average rating for each provider
        for p in providers:
            ratings = list(db.ratings.find({"provider_id": str(p["_id"])}))
            if ratings:
                avg = sum(r["rating"] for r in ratings) / len(ratings)
                p["avg_rating"] = round(avg, 1)
                p["rating_count"] = len(ratings)
            else:
                p["avg_rating"] = None
                p["rating_count"] = 0
    categories = list(db.categories.find())
    return render_template("search.html", providers=providers, message=message, categories=categories)

# ---------------- BOOK ----------------
@app.route("/book/<provider_id>", methods=["GET", "POST"])
@login_required
def book(provider_id):
    # show form if GET
    if request.method == "POST":
        message = request.form.get("message", "")
        db.bookings.insert_one({
            "user_id": session["user_id"],
            "provider_id": provider_id,
            "status": "Pending",
            "date": datetime.now(),
            "message": message
        })
        return redirect("/bookings")
    # GET should render profile / booking form
    provider = db.users.find_one({"_id": ObjectId(provider_id)})
    ratings = list(db.ratings.find({"provider_id": provider_id}))
    return render_template("provider_profile.html", provider=provider, ratings=ratings)

# ---------------- BOOKINGS ----------------
@app.route("/bookings")
@login_required
def bookings():
    if session["role"] == "user":
        data = list(db.bookings.find({"user_id": session["user_id"]}))
    else:
        # include user name in provider view
        data = []
        for b in db.bookings.find({"provider_id": session["user_id"]}):
            user = db.users.find_one({"_id": ObjectId(b["user_id"])})
            b["user_name"] = user.get("name") if user else ""
            data.append(b)
    return render_template("bookings.html", bookings=data)

# ---------------- UPDATE BOOKING ----------------
@app.route("/update_booking/<id>/<status>")
@login_required
def update_booking(id, status):
    db.bookings.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"status": status}}
    )
    return redirect("/provider_dash")

# ---------------- MARK COMPLETED ----------------
@app.route("/mark_completed/<id>")
@login_required
def mark_completed(id):
    db.bookings.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"completed": True}}
    )
    return redirect("/provider_dash")

# ---------------- REVIEW ----------------
@app.route("/review/<booking_id>", methods=["GET", "POST"])
@login_required
def review(booking_id):
    booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
    
    if not booking or booking["user_id"] != session["user_id"]:
        return "Unauthorized"
    
    if not booking.get("completed"):
        return "Service not completed yet"
    
    if request.method == "POST":
        db.ratings.insert_one({
            "provider_id": booking["provider_id"],
            "user_id": session["user_id"],
            "booking_id": booking_id,
            "rating": int(request.form["rating"]),
            "comment": request.form["comment"],
            "date": datetime.now()
        })
        return redirect("/user_dash")

    return render_template("review.html", booking=booking)

# ---------------- RATINGS PAGE ----------------
@app.route("/ratings/<provider_id>")
def ratings_page(provider_id):
    provider = db.users.find_one({"_id": ObjectId(provider_id)})
    ratings = list(db.ratings.find({"provider_id": provider_id}))
    
    # get user names for ratings
    for r in ratings:
        user = db.users.find_one({"_id": ObjectId(r["user_id"])})
        r["user_name"] = user.get("name") if user else "Anonymous"
        r["completed"] = True
    
    return render_template("ratings_page.html", provider=provider, ratings=ratings)
@app.route("/complaint", methods=["GET", "POST"])
@login_required
def complaint():
    if request.method == "POST":
        db.complaints.insert_one({
            "user_id": session["user_id"],
            "text": request.form["text"],
            "date": datetime.now()
        })
        return redirect("/user_dash")

    return render_template("complaint.html")

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run(debug=True)