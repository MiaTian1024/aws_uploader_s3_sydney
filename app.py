from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request, Security, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
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
    AWS_SESSION_TOKEN: Optional[str] = None  # Optional with None default
    S3_BUCKET_NAME: str
    S3_REGION: str = "ap-southeast-2"
    ALLOWED_ORIGINS: str = "*"
    BUBBLE_APP_URL: str  

    class Config:
        env_file = ".env"

app = FastAPI()
settings = Settings()
security = HTTPBearer()
handler = Mangum(app)

app.add_middleware(
    CORSMiddleware,
    # allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=8000  # Cache preflight requests
)

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    aws_session_token=settings.AWS_SESSION_TOKEN,
    region_name=settings.S3_REGION
)

async def verify_bubble_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> Dict:
    """Verify the Bubble authentication token"""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="No authorization token provided"
        )
    
    try:
        # Get and format token
        token = credentials.credentials
        if not token.startswith('Bearer '):
            token = f'Bearer {token}'
        
        # Make request to Bubble's API
        headers = {
            'Authorization': token,
            'Content-Type': 'application/json'
        }
        
        # Changed to POST request and ensure HTTPS
        bubble_url = settings.BUBBLE_APP_URL
        if not bubble_url.startswith('https://'):
            bubble_url = f"https://{bubble_url.replace('http://', '')}"
            
        response = requests.post(
            f"{bubble_url}/api/1.1/wf/verify-user-token",
            headers=headers,
            json={},  # Empty JSON body for POST request
            timeout=5  # Add timeout to prevent hanging
        )

        # Print response for debugging
        # print(f"Verification Response Status: {response.status_code}")
        # print(f"Verification Response: {response.text}")
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail=f"Invalid or expired token. Status: {response.status_code}, Response: {response.text}"
            )
            
        response_data = response.json()

        # Extract user data from the nested structure
        if (response_data.get('status') == 'success' and 
            response_data.get('response', {}).get('user', {}).get('_id')):
                
            user_data = response_data['response']['user']
            return {
                '_id': user_data['_id'],
                'name': user_data.get('Name'),
                'workspace': user_data.get('Workspace'),
                'email': user_data.get('authentication', {}).get('email', {}).get('email'),
                'role': user_data.get('Role')
            }
        else:
            raise HTTPException(
                status_code=401,
                detail="Could not extract user data from response"
            )
        
    except requests.RequestException as e:
        raise HTTPException(
            status_code=401,
            detail=f"Failed to connect to Bubble API: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Token verification failed: {str(e)}"
        )

@app.post("/upload")
@app.post("/upload/")
async def upload_file(
    file: UploadFile = File(...),
    filename: str = None, 
    user_data: Dict = Depends(verify_bubble_token),
    credentials: HTTPAuthorizationCredentials = Security(security)  
):
    try:
        # Get user ID from verified user data
        user_id = str(user_data.get('_id'))
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="User ID not found in verified data"
            )
        print(user_id)

        # Generate timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Get original file extension
        original_extension = os.path.splitext(file.filename)[1]

        # Handle custom filename
        if filename:
            # Remove any potentially problematic characters
            safe_filename = ''.join(c for c in filename if c.isalnum() or c in '._- ')
            
            # Check if custom filename has an extension
            filename_base, custom_extension = os.path.splitext(safe_filename)
            
            # If no extension in custom filename, add the original extension
            if not custom_extension:
                safe_filename = f"{filename_base}{original_extension}"
        else:
            filename_base, extension = os.path.splitext(file.filename)
            safe_filename = file.filename
            
        # Add timestamp to filename while preserving the extension
        timestamped_filename = f"{os.path.splitext(safe_filename)[0]}_{timestamp}{original_extension}"
        
        # Generate new filename with user ID path
        new_filename = f"{user_id}/{timestamped_filename}"
        
        # Get user Tool from verified user data, use this as folder name
        folder = str(user_data.get('workspace'))
        print(folder)
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
            Metadata={
                'user_id': str(user_id),
                'original_filename': file.filename,
                'custom_filename': safe_filename,
                'upload_timestamp': timestamp
            }
        )
        
        # Generate permanent S3 URL
        file_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.S3_REGION}.amazonaws.com/{new_filename}"
        
        # Save to Bubble after successful S3 upload
        bubble_url = settings.BUBBLE_APP_URL
        if not bubble_url.startswith('https://'):
            bubble_url = f"https://{bubble_url.replace('http://', '')}"
        bubble_save_url = f"{bubble_url}/api/1.1/wf/save-s3-url"

        token = credentials.credentials  # Get the original token

        headers = {
            'Authorization': f"Bearer {token}",
            'Content-Type': 'application/json'
        }

        print(f"Headers being sent: {headers}")
            
        bubble_payload = {
            "file_name": safe_filename,
            "file_url": file_url,
            "user_id": user_id
        }
            
        bubble_response = requests.post(
            bubble_save_url,
            headers=headers,
            json=bubble_payload
        )
            
        if bubble_response.status_code != 200:
            print(f"Warning: Failed to save to Bubble. Status: {bubble_response.status_code}, Response: {bubble_response.text}")
            
        return JSONResponse(
            status_code=200,
            content={
                "message": "File uploaded successfully",
                "filename": safe_filename,
                "timestamped_filename": timestamped_filename,
                "path": new_filename,
                "url": file_url,
                "user_id": user_id,
                "folder": folder,
                "timestamp": timestamp,
                "bubble_save_status": "success" if bubble_response.status_code == 200 else "failed",
                "bubble_response": bubble_response.json() if bubble_response.status_code == 200 else None
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


@app.post("/get-upload-url")
@app.post("/get-upload-url/")
async def get_upload_url(
    filename: str,
    user_data: Dict = Depends(verify_bubble_token),
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    try:
        user_id = str(user_data.get('_id'))
        if not user_id:
            raise HTTPException(status_code=401, detail="User ID not found in verified data")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = ''.join(c for c in filename if c.isalnum() or c in '._- ')
        timestamped_filename = f"{os.path.splitext(safe_filename)[0]}_{timestamp}{os.path.splitext(safe_filename)[1]}"
        
        folder = str(user_data.get('workspace'))
        new_filename = f"{folder}/{user_id}/{timestamped_filename}" if folder else f"{user_id}/{timestamped_filename}"

        # Create S3 client with explicit credentials including session token
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            aws_session_token=settings.AWS_SESSION_TOKEN,  # This is required for temporary credentials
            region_name=settings.S3_REGION
        )

        # Generate presigned URL with session token included
        client_method_kwargs = {
            'Bucket': settings.S3_BUCKET_NAME,
            'Key': new_filename,
        }

        # Add security token to the request
        url = s3_client.generate_presigned_url(
            ClientMethod='put_object',
            Params=client_method_kwargs,
            ExpiresIn=3600,
        )

        # Generate the final S3 URL
        final_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.S3_REGION}.amazonaws.com/{new_filename}"

        return JSONResponse(
            status_code=200,
            content={
                "upload_url": url,
                "final_url": final_url,
                "filename": safe_filename,
                "path": new_filename,
                "user_id": user_id,
                "folder": folder,
                "timestamp": timestamp
            }
        )

    except Exception as e:
        # Print detailed error for debugging
        print(f"Error details: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@app.post("/confirm-upload")
@app.post("/confirm-upload/")
async def confirm_upload(
    file_url: str,
    filename: str,
    user_data: Dict = Depends(verify_bubble_token),
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    try:
        user_id = str(user_data.get('_id'))
        
        # Save to Bubble
        bubble_url = settings.BUBBLE_APP_URL
        if not bubble_url.startswith('https://'):
            bubble_url = f"https://{bubble_url.replace('http://', '')}"
        bubble_save_url = f"{bubble_url}/api/1.1/wf/save-s3-url"

        headers = {
            'Authorization': f"Bearer {credentials.credentials}",
            'Content-Type': 'application/json'
        }
            
        bubble_payload = {
            "file_name": filename,
            "file_url": file_url,
            "user_id": user_id
        }
            
        bubble_response = requests.post(
            bubble_save_url,
            headers=headers,
            json=bubble_payload
        )
            
        return JSONResponse(
            status_code=200,
            content={
                "message": "Upload confirmed and saved to Bubble",
                "bubble_save_status": "success" if bubble_response.status_code == 200 else "failed",
                "bubble_response": bubble_response.json() if bubble_response.status_code == 200 else None
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)