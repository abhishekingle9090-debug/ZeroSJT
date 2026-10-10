from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prisma import Prisma
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta

# Security Configuration
SECRET_KEY = "vit_super_secret_key_change_in_production"
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="ZeroSJT Code Judge API", version="1.0.0")
prisma = Prisma()

@app.on_event("startup")
async def startup():
    await prisma.connect()

@app.on_event("shutdown")
async def shutdown():
    await prisma.disconnect()

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
    # 1. Find the user
    db_user = await prisma.user.find_unique(where={"email": user.email})
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    # 2. Verify the password
    if not pwd_context.verify(user.password, db_user.password):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    # 3. Generate JWT Token
    expire = datetime.utcnow() + timedelta(hours=2)
    payload = {"sub": db_user.id, "email": db_user.email, "exp": expire}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    return {"access_token": token, "token_type": "bearer"}


# Add this data model near your other classes
class CodeSubmit(BaseModel):
    problem_id: str
    user_id: str
    language_id: int
    source_code: str

# Add this route at the bottom of the file
@app.post("/submit")
async def submit_code(submission: CodeSubmit):
    # 1. Save the submission to the database as "PENDING"
    new_submission = await prisma.submission.create(
        data={
            "problemId": submission.problem_id,
            "userId": submission.user_id,
            "languageId": submission.language_id,
            "code": submission.source_code,
            "status": "PENDING"
        }
    )
    
    # 2. [Next Step] Send to Redis Queue for Judge0 here...
    
    return {
        "message": "Submission received!",
        "submission_id": new_submission.id,
        "status": "PENDING"
    }