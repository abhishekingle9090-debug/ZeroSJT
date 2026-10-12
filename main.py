from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from prisma import Prisma
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta
import redis.asyncio as redis
import json

# Security Configuration
SECRET_KEY = "vit_super_secret_key_change_in_production"
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="ZeroSJT Code Judge API", version="1.0.0")
prisma = Prisma()

# Set up a global variable for Redis
redis_client = None

@app.on_event("startup")
async def startup():
    global redis_client
    await prisma.connect()
    
    # Connect to Redis
    try:
        redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)
        await redis_client.ping()
        print("✅ Connected to Redis Queue")
    except Exception as e:
        print("⚠️ Redis not found (Waiting for Yash's container!)")

@app.on_event("shutdown")
async def shutdown():
    await prisma.disconnect()
    if redis_client:
        await redis_client.close()

@app.get("/health")
def health_check():
    return {"status": "ok"}

# --- Data Models ---
class UserCreate(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class CodeSubmit(BaseModel):
    problem_id: str
    user_id: str
    language_id: int
    source_code: str

class Judge0Webhook(BaseModel):
    status: dict
    stdout: str | None = None
    stderr: str | None = None
    compile_output: str | None = None
class ProblemCreate(BaseModel):
    id: str  # e.g., "two-sum"
    title: str
    description: str
    difficulty: str  # "Easy", "Medium", "Hard"

class ProblemResponse(BaseModel):
    id: str
    title: str
    description: str
    difficulty: str

# --- Routes ---
@app.post("/register")
async def register_user(user: UserCreate):
    existing_user = await prisma.user.find_unique(where={"email": user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_password = pwd_context.hash(user.password)
    new_user = await prisma.user.create(
        data={"email": user.email, "password": hashed_password}
    )
    return {"message": "User created successfully!", "user_id": new_user.id}

@app.post("/login")
async def login_user(user: UserLogin):
    db_user = await prisma.user.find_unique(where={"email": user.email})
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    if not pwd_context.verify(user.password, db_user.password):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    expire = datetime.utcnow() + timedelta(hours=2)
    payload = {"sub": db_user.id, "email": db_user.email, "exp": expire}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    return {"access_token": token, "token_type": "bearer"}
# --- ADMIN ROUTE: Add a new problem ---
@app.post("/admin/problems")
async def create_problem(problem: ProblemCreate):
    new_problem = await prisma.problem.create(
        data={
            "id": problem.id,
            "title": problem.title,
            "description": problem.description,
            "difficulty": problem.difficulty
        }
    )
    return {"message": "Problem created successfully!", "problem": new_problem}

# --- FRONTEND ROUTE: Get all problems ---
@app.get("/problems")
async def get_all_problems():
    problems = await prisma.problem.find_many()
    return problems

# --- FRONTEND ROUTE: Get a specific problem ---
@app.get("/problems/{problem_id}")
async def get_problem(problem_id: str):
    problem = await prisma.problem.find_unique(where={"id": problem_id})
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")
    return problem

# --- THE REDIS QUEUE PUSHER ---
async def push_to_queue(submission_id: str, problem_id: str, source_code: str, language_id: int):
    await prisma.submission.update(
        where={"id": submission_id},
        data={"status": "IN_QUEUE"}
    )
    
    job_data = {
        "submission_id": submission_id,
        "problem_id": problem_id,
        "source_code": source_code,
        "language_id": language_id
    }
    
    if redis_client:
        await redis_client.lpush("judge0_queue", json.dumps(job_data))
        print(f"🚀 SUCCESS: Pushed submission {submission_id} to Redis!")
    else:
        print("⚠️ ERROR: Redis is not connected!")

# --- SUBMIT ROUTE WITH RATE LIMITING ---
@app.post("/submit")
async def submit_code(submission: CodeSubmit, background_tasks: BackgroundTasks):
    # 1. REDIS RATE LIMITING
    if redis_client:
        rate_key = f"rate_limit:{submission.user_id}"
        request_count = await redis_client.incr(rate_key)
        
        if request_count == 1:
            await redis_client.expire(rate_key, 60)
            
        if request_count > 5:
            raise HTTPException(status_code=429, detail="Rate limit exceeded! Please wait 60 seconds.")

    # 2. Save the submission to the database as "PENDING"
    new_submission = await prisma.submission.create(
        data={
            "problemId": submission.problem_id,
            "userId": submission.user_id,
            "languageId": submission.language_id,
            "code": submission.source_code,
            "status": "PENDING"
        }
    )
    
    # 3. Send to our REAL Redis queue in the background
    background_tasks.add_task(
        push_to_queue,
        new_submission.id,
        submission.problem_id,
        submission.source_code,
        submission.language_id
    )
    
    return {
        "message": "Submission received and queued!",
        "submission_id": new_submission.id,
        "status": "QUEUED"
    }

# --- WEBHOOK ROUTE ---
@app.put("/webhook/{submission_id}")
async def judge_webhook(submission_id: str, result: Judge0Webhook):
    final_status = result.status.get("description", "ERROR").upper()
    
    updated_submission = await prisma.submission.update(
        where={"id": submission_id},
        data={"status": final_status}
    )
    
    print(f"🔔 WEBHOOK: Submission {submission_id} is now {final_status}!")
    return {"message": "Database updated successfully"}

# --- GET STATUS ROUTE ---
@app.get("/status/{submission_id}")
async def get_submission_status(submission_id: str):
    submission = await prisma.submission.find_unique(where={"id": submission_id})
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    return {
        "submission_id": submission.id,
        "status": submission.status
    }
    # --- FRONTEND ROUTE: Get User's Submission History ---
@app.get("/users/{user_id}/submissions")
async def get_user_submissions(user_id: str):
    submissions = await prisma.submission.find_many(
        where={
            "userId": user_id
        },
        order={
            "createdAt": "desc"
        }
    )
    
    if not submissions:
        return {"count": 0, "submissions": []}
        
    return {
        "count": len(submissions),
        "submissions": submissions
    }
    # --- FRONTEND ROUTE: Global Leaderboard ---
@app.get("/leaderboard")
async def get_leaderboard():
    # 1. Fetch only successful submissions
    submissions = await prisma.submission.find_many(
        where={
            "status": "ACCEPTED"
        }
    )
    
    # 2. Tally up the scores per user
    scores = {}
    for sub in submissions:
        scores[sub.userId] = scores.get(sub.userId, 0) + 1
        
    # 3. Sort from highest score to lowest
    ranked_users = [
        {"userId": user, "accepted_count": count} 
        for user, count in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]
    
    return {"leaderboard": ranked_users}
# --- FRONTEND ROUTE: User Profile Stats ---
@app.get("/users/{user_id}/stats")
async def get_user_stats(user_id: str):
    # Fetch all submissions for this user
    submissions = await prisma.submission.find_many(
        where={"userId": user_id}
    )
    
    total_attempts = len(submissions)
    accepted_attempts = len([sub for sub in submissions if sub.status == "ACCEPTED"])
    
    # Calculate win rate (prevent dividing by zero)
    win_rate = (accepted_attempts / total_attempts * 100) if total_attempts > 0 else 0
    
    return {
        "userId": user_id,
        "total_attempts": total_attempts,
        "accepted_attempts": accepted_attempts,
        "win_rate_percentage": round(win_rate, 2)
    }