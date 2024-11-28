from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request, Security, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
# from fastapi.openapi.models import SecuritySchemeInHeader
from mangum import Mangum
import boto3
from botocore.exceptions import ClientError
import os
from typing import Optional, Dict
from pydantic_settings import BaseSettings
from datetime import datetime
import requests

class Settings(BaseSettings):
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    S3_BUCKET_NAME: str
    S3_REGION: str = "ap-southeast-2"
    ALLOWED_ORIGINS: str = "*"
    BUBBLE_APP_URL: str  # Your Bubble app URL

    class Config:
        env_file = ".env"

app = FastAPI()
settings = Settings()
security = HTTPBearer()
handler = Mangum(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.S3_REGION
)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """Extract and verify the Bubble token"""
    try:
        token = credentials.credentials
        # Extract the user ID from the Bubble token (bus|userId|timestamp)
        user_id = token.split('|')[1]
        return user_id
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token format: {str(e)}"
        )

async def verify_bubble_token(authorization: str = Header(None)) -> Dict:
    """Verify the Bubble authentication token"""
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="No authorization token provided"
        )
    
    # Remove 'Bearer ' prefix if present
    token = authorization
    
    # Verify token with Bubble
    try:
        headers = {
            'Authorization': token
        }
        # Make request to Bubble's API to get current user
        response = requests.get(
            f"{settings.BUBBLE_APP_URL}/api/1.1/wf/verify-user-token",
            headers=headers
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired token"
            )
            
        user_data = response.json()
        return user_data
        
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Token verification failed: {str(e)}"
        )


@app.post("/upload/")
async def upload_file(
    file: UploadFile = File(...),
    folder: Optional[str] = None,
    user_id: str = Depends(get_current_user)
    # user_data: Dict = Depends(verify_bubble_token)
):
    try:
        # Get user information from Bubble
        # user_id = user_data.get('_id')  # Bubble user ID
        # user_email = user_data.get('email', 'unknown')  # User email if available

        # Get user ID from token
        # user_id = await get_current_user(request)
        
        # Generate a unique file name using timestamp and user ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_filename = file.filename
        file_extension = os.path.splitext(original_filename)[1]
        new_filename = f"{user_id}/{timestamp}{file_extension}"
        
        # If folder is specified, prepend it to the filename
        if folder:
            new_filename = f"{folder}/{new_filename}"
        
        # Read file contents
        file_contents = await file.read()
        
        # Upload to S3 with public-read ACL
        s3_client.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=new_filename,
            Body=file_contents,
            ContentType=file.content_type,
            ACL='public-read',
            Metadata={
                'user_id': user_id,
                # 'user_email': user_email,
                'upload_timestamp': timestamp
            }
        )
        
        # Generate permanent S3 URL
        file_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.S3_REGION}.amazonaws.com/{new_filename}"
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "File uploaded successfully",
                "filename": new_filename,
                "url": file_url,
                "user_id": user_id,
                # "user_email": user_email,
                "upload_timestamp": timestamp
            }
        )
        
    except ClientError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error uploading file to S3: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred: {str(e)}"
        )


@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)