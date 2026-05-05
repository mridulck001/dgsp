# 1. Use an official lightweight Python runtime as the base image
FROM python:3.9-slim

# 2. Prevent apt-get from prompting for user input during installation
ENV DEBIAN_FRONTEND=noninteractive

# 3. Install system dependencies (ffmpeg is strictly required for pydub to process >30s audio)
# We use --no-install-recommends and clear the apt cache to keep the container size small
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 4. Set the working directory inside the container
WORKDIR /app

# 5. Copy the requirements.txt file first 
# (Doing this before copying the rest of the app leverages Docker caching, making rebuilds faster)
COPY requirements.txt .

# 6. Install the Python dependencies listed in your requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 7. Copy the rest of your application files (app.py, templates/, etc.) into the container
COPY . .

# 8. Expose port 7860, which is the exact port Hugging Face Spaces listens to by default
EXPOSE 7860

# 9. Set the environment variables required to run Flask on the correct port and host
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=7860

# 10. Start the Flask application
CMD ["python", "app.py"]
