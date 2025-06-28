# Stage 1: Build telegram-bot-api
FROM ubuntu:latest AS api_builder

WORKDIR /usr/src/telegram-bot-api

# Install build dependencies for telegram-bot-api
# Referencing tdlib and telegram-bot-api build instructions
# https://github.com/tdlib/td
# https://github.com/tdlib/telegram-bot-api
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    cmake \
    g++ \
    libssl-dev \
    zlib1g-dev \
    libjson-c-dev \
    libreadline-dev \
    libconfig++-dev \
    && rm -rf /var/lib/apt/lists/*

# Clone tdlib
RUN git clone https://github.com/tdlib/td.git && \
    cd td && \
    git checkout tags/v1.8.0 # Use a stable tag for tdlib if possible, or latest if no specific requirement

# Build tdlib
RUN mkdir -p td/build && \
    cd td/build && \
    cmake -DCMAKE_BUILD_TYPE=Release .. && \
    cmake --build . --target prepare_description --config Release

# Clone telegram-bot-api
RUN git clone https://github.com/tdlib/telegram-bot-api.git && \
    cd telegram-bot-api && \
    git checkout tags/v7.6 # Use a stable tag for telegram-bot-api, or latest

# Build telegram-bot-api
RUN mkdir -p telegram-bot-api/build && \
    cd telegram-bot-api/build && \
    cmake -DCMAKE_BUILD_TYPE=Release -DTD_INCLUDE_DIR=/usr/src/telegram-bot-api/td -DTD_AUTO_UPDATE=Off .. && \
    cmake --build . --config Release

# Stage 2: Final image for bot and API server
FROM python:3.10-slim

WORKDIR /app

# Install ffmpeg and other necessary utilities
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the built telegram-bot-api executable from the builder stage
COPY --from=api_builder /usr/src/telegram-bot-api/telegram-bot-api/build/telegram-bot-api /usr/local/bin/telegram-bot-api

# Copy the rest of the application code
COPY . /app

# Expose the port for the Bot API server
EXPOSE 8080

# The CMD will be handled by main.py
CMD ["python", "-m", "src.core.main"] 