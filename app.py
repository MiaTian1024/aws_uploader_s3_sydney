from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import boto3
from botocore.exceptions import ClientError
import os
from typing import Optional
from pydantic_settings import BaseSettings
from datetime import datetime

class Settings(BaseSettings):
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AWS_BUCKET_NAME: str
    AWS_REGION: str = "ap-southeast-2"

    class Config:
        env_file = ".env"

app = FastAPI()
settings = Settings()

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION
)

@app.post("/upload/")
async def upload_file(
    file: UploadFile = File(...),
    folder: Optional[str] = None
):
    try:
        # Generate a unique file name using timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_filename = file.filename
        file_extension = os.path.splitext(original_filename)[1]
        new_filename = f"{timestamp}{file_extension}"
        
        # If folder is specified, prepend it to the filename
        if folder:
            new_filename = f"{folder}/{new_filename}"
        
        # Read file contents
        file_contents = await file.read()
        
        # Upload to S3
        s3_client.put_object(
            Bucket=settings.AWS_BUCKET_NAME,
            Key=new_filename,
            Body=file_contents,
            ContentType=file.content_type
        )
        
        # Generate a pre-signed URL for the uploaded file
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_BUCKET_NAME,
                'Key': new_filename
            },
            ExpiresIn=3600  # URL expires in 1 hour
        )
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "File uploaded successfully",
                "filename": new_filename,
                "temporary_url": url
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