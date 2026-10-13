from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from prisma import Prisma
from typing import Optional, List
import httpx
from datetime import datetime, timedelta
import httpx
# NOTE: You installed these on Days 1 & 2. 
# If any are missing, run: pip install passlib[bcrypt] python-jose redis httpx
from passlib.context import CryptContext
from jose import JWTError, jwt
import redis.asyncio as redis

# --- CONFIGURATION ---
SECRET_KEY = "your-super-secret-jwt-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
JUDGE0_URL = "http://localhost:2358" # Yash's Local Judge0 URL

app = FastAPI(title="TIC-Contest Backend API")
prisma = Prisma()
#redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- LIFECYCLE EVENTS ---
@app.on_event("startup")
async def startup():
    await prisma.connect()
    print("INFO: Prisma connected. (Redis completely bypassed for local testing)")

@app.on_event("shutdown")
async def shutdown():
    await prisma.disconnect()
    # await redis_client.close()  <-- Commented out!
# --- PYDANTIC SCHEMAS ---
class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class ProblemCreate(BaseModel):
    title: str
    description: str
    difficulty: str

class SubmitCode(BaseModel):
    source_code: str
    language_id: int
    problem_id: int
    user_id: int

class TestCaseCreate(BaseModel):
    input: str
    expected_output: str
    is_hidden: bool = False


# --- AUTHENTICATION UTILS ---
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# --- SYSTEM ROUTES ---
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "TIC-Contest Gateway Active"}

# --- AUTH ROUTES ---
@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register_user(user: UserCreate):
    existing_user = await prisma.user.find_unique(where={"email": user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_pw = get_password_hash(user.password)
    new_user = await prisma.user.create(
        data={
            "username": user.username,
            "email": user.email,
            "password": hashed_pw 
        }
    )
    return {"message": "User created successfully", "user_id": new_user.id}

@app.post("/login")
async def login_user(user: UserLogin):
    db_user = await prisma.user.find_unique(where={"email": user.email})
    if not db_user or not verify_password(user.password, db_user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    access_token = create_access_token(data={"sub": str(db_user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

# --- PROBLEM ROUTES (WITH PAGINATION & FILTERS) ---
@app.post("/admin/problems", status_code=status.HTTP_201_CREATED)
async def create_problem(problem: ProblemCreate):
    new_problem = await prisma.problem.create(
        data={
            "title": problem.title,
            "description": problem.description,
            "difficulty": problem.difficulty
        }
    )
    return {"message": "Problem created", "problem_id": new_problem.id}
@app.post("/problems/{problem_id}/testcases", status_code=status.HTTP_201_CREATED)
async def create_test_case(problem_id: int, test_case: TestCaseCreate):
    problem = await prisma.problem.find_unique(where={"id": problem_id})
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")

    new_test_case = await prisma.testcase.create(
        data={
            "problem_id": problem_id,
            "input": test_case.input,
            "expected_output": test_case.expected_output,
            "is_hidden": test_case.is_hidden
        }
    )
    return {"message": "Test case added successfully", "data": new_test_case}
@app.get("/problems")
async def get_problems(difficulty: str | None = None, skip: int = 0, limit: int = 10):
    query_filter = {}
    if difficulty:
        query_filter = {"difficulty": difficulty.capitalize()}
    
    problems = await prisma.problem.find_many(
        where=query_filter,
        skip=skip,
        take=limit
    )
    
    return {
        "skip": skip,
        "limit": limit,
        "count": len(problems),
        "problems": problems
    }


@app.get("/problems/{problem_id}/testcases")
async def get_test_cases(problem_id: int, include_hidden: bool = False):
    query_filter = {"problem_id": problem_id}
    if not include_hidden:
        query_filter["is_hidden"] = False

    test_cases = await prisma.testcase.find_many(where=query_filter)
    return {"total": len(test_cases), "data": test_cases}

@app.get("/problems")
async def get_problems(skip: int = 0, limit: int = 10, difficulty: Optional[str] = None):
    query_filter = {}
    if difficulty:
        query_filter["difficulty"] = difficulty

    problems = await prisma.problem.find_many(
        skip=skip,
        take=limit,
        where=query_filter
    )
    total_count = await prisma.problem.count(where=query_filter)

    return {
        "total": total_count,
        "skip": skip,
        "limit": limit,
        "data": problems
    }

@app.get("/problems/{problem_id}")
async def get_problem(problem_id: int):
    problem = await prisma.problem.find_unique(where={"id": problem_id})
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")
    return problem

# --- TEST CASE ROUTES (NEW FOR DAY 4) ---
@app.post("/problems/{problem_id}/testcases", status_code=status.HTTP_201_CREATED)
async def create_test_case(problem_id: int, test_case: TestCaseCreate):
    problem = await prisma.problem.find_unique(where={"id": problem_id})
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")

    new_test_case = await prisma.testcase.create(
        data={
            "problem_id": problem_id,
            "input": test_case.input,
            "expected_output": test_case.expected_output,
            "is_hidden": test_case.is_hidden
        }
    )
    return {"message": "Test case added successfully", "data": new_test_case}



# --- EXECUTION ENGINE & REDIS ROUTES ---
@app.post("/submit")
async def submit_code(submission: SubmitCode):
    # 1. Fetch Test Cases
    test_cases = await prisma.testcase.find_many(
        where={"problem_id": submission.problem_id}
    )
    if not test_cases:
        raise HTTPException(status_code=400, detail="This problem has no test cases!")

    # 2. Save Pending Submission to DB
    new_sub = await prisma.submission.create(
        data={
            "code": submission.source_code,
            "languageId": submission.language_id,
            "problemId": str(submission.problem_id),
            "userId": str(submission.user_id),
            "status": "Pending"
        }
    )

    # 3. Try sending to Judge0 (with fallback if offline!)
    webhook_url = f"http://host.docker.internal:8000/webhook/{new_sub.id}"
    judge0_payload = {
        "source_code": submission.source_code,
        "language_id": submission.language_id,
        "stdin": test_cases[0].input,
        "expected_output": test_cases[0].expected_output,
        "callback_url": webhook_url
    }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(
                "http://localhost:2358/submissions?base64_encoded=false",
                json=judge0_payload
            )
            if response.status_code == 201:
                print(f"🚀 Sent to Judge0! Token: {response.json().get('token')}")
            else:
                print(f"⚠️ Judge0 returned status {response.status_code}")
    except httpx.ConnectError:
        print("⚠️ WARNING: Judge0 is offline. Submission saved to DB, but code execution was skipped.")

    return {
        "message": "Code submitted successfully!", 
        "submission_id": new_sub.id
    }
# 1. Create a strict Pydantic model for the webhook body
class WebhookPayload(BaseModel):
    status: dict

# 2. Force submission_id to be a string, and payload to use the model
@app.put("/webhook/{submission_id}")
async def judge0_webhook(submission_id: str, payload: WebhookPayload):
    final_grade = payload.status.get("description", "Unknown Error")

    existing_sub = await prisma.submission.find_unique(where={"id": submission_id})
    if not existing_sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    updated_sub = await prisma.submission.update(
        where={"id": submission_id},
        data={"status": final_grade}
    )

    print(f"🎯 GRADING COMPLETE | Submission: {submission_id} | Result: {final_grade}")
    return {"message": "Webhook processed successfully"}

@app.get("/status/{submission_id}")
async def get_submission_status(submission_id: int):
    sub = await prisma.submission.find_unique(where={"id": submission_id})
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    return {"status": sub.status}

# --- LEADERBOARD & STATS ROUTES ---
@app.get("/users/{user_id}/stats")
async def get_user_stats(user_id: int):
    # Fetch all submissions for the user
    subs = await prisma.submission.find_many(
        where={"userId": str(user_id)}
    )
    
    total = len(subs)
    accepted = sum(1 for s in subs if s.status == "Accepted")
    success_rate = (accepted / total * 100) if total > 0 else 0.0

    return {
        "user_id": user_id,
        "total_submissions": total,
        "accepted_submissions": accepted,
        "success_rate_percentage": round(success_rate, 2)
    }

@app.get("/leaderboard")
async def get_leaderboard():
    # 1. Fetch all successful submissions
    accepted_subs = await prisma.submission.find_many(
        where={"status": "Accepted"}
    )
    
    # 2. Count unique problems solved per user
    user_solved_problems = {}
    for sub in accepted_subs:
        uid = sub.userId
        if uid not in user_solved_problems:
            user_solved_problems[uid] = set()
        user_solved_problems[uid].add(sub.problemId)
        
    # 3. Build leaderboard list
    leaderboard = [
        {"user_id": uid, "problems_solved": len(problems)}
        for uid, problems in user_solved_problems.items()
    ]
    
    # 4. Sort by highest problems solved
    leaderboard.sort(key=lambda x: x["problems_solved"], reverse=True)
    
    return {
        "total_ranked_users": len(leaderboard),
        "leaderboard": leaderboard
    }
@app.get("/users/{user_id}/stats")
async def get_user_stats(user_id: int):
    total_subs = await prisma.submission.count(where={"user_id": user_id})
    accepted = await prisma.submission.count(where={"user_id": user_id, "status": "Accepted"})
    return {
        "user_id": user_id,
        "total_submissions": total_subs,
        "accepted": accepted,
        "accuracy": f"{(accepted/total_subs)*100:.2f}%" if total_subs > 0 else "0%"
    }
@app.put("/webhook/{submission_id}")
async def judge0_webhook(submission_id: str, result: dict):
    # 1. Extract the grade description (e.g., "Accepted", "Wrong Answer", "Compilation Error")
    status_info = result.get("status", {})
    final_grade = status_info.get("description", "Unknown Error")

    # 2. Find the submission in the database
    existing_sub = await prisma.submission.find_unique(where={"id": submission_id})
    if not existing_sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    # 3. Update the database with the final grade
    updated_sub = await prisma.submission.update(
        where={"id": submission_id},
        data={"status": final_grade}
    )

    # Print to your terminal so you can see it working live!
    print(f"🎯 GRADING COMPLETE | Submission: {submission_id} | Result: {final_grade}")
    
    return {"message": "Webhook processed successfully"}
# 1. Fetch a specific submission by its ID (Great for result pages)
@app.get("/submissions/{submission_id}")
async def get_submission(submission_id: str):
    sub = await prisma.submission.find_unique(
        where={"id": submission_id},
        include={"problem": True}  # Pulls related problem info if linked in Prisma schema
    )
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    return {
        "submission_id": sub.id,
        "code": sub.code,
        "language_id": sub.languageId,
        "status": sub.status,
        "problem_id": sub.problemId,
        "user_id": sub.userId,
        "created_at": sub.createdAt
    }

# 2. Fetch all submission history for a specific user
@app.get("/users/{user_id}/submissions")
async def get_user_submissions(user_id: int, skip: int = 0, limit: int = 10):
    subs = await prisma.submission.find_many(
        where={"userId": str(user_id)},
        order={"createdAt": "desc"},
        skip=skip,
        take=limit
    )
    
    return {
        "user_id": user_id,
        "skip": skip,
        "limit": limit,
        "count": len(subs),
        "submissions": subs
    }