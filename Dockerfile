# Use official Python runtime as base image
FROM python:3.12.12-alpine

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies
# Alpine uses 'apk' instead of 'apt-get'. 
# Adding build dependencies for potential compiled requirements.
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose the standard WSS port
EXPOSE 443

# Command to run the application using Gunicorn
CMD ["gunicorn", "-c", "gunicorn_conf.py", "main:app"]
