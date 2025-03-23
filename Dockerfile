FROM python:3.9-slim

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY *.py ./

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the worker with the bear
CMD ["python","run.py"]