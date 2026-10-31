FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-cryptography ca-certificates curl libgomp1 libcurl4t64 libssl3t64 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/msw
RUN curl -fL --max-time 180 -o /tmp/llama.tgz https://github.com/ggml-org/llama.cpp/releases/download/b11979/llama-b11979-bin-ubuntu-x64.tar.gz \
 && echo '437879071395165dafd08bbd70f9eb9b0353698b5278d09504a2a97b46e81130  /tmp/llama.tgz' | sha256sum -c - \
 && mkdir llama && tar -xzf /tmp/llama.tgz --strip-components=1 -C llama && rm /tmp/llama.tgz
RUN curl -fL --max-time 240 -o model.gguf https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/09816acd5d99df7be770d85ea30822623dab342c/SmolLM2-135M-Instruct-Q4_K_M.gguf \
 && echo '2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d  model.gguf' | sha256sum -c -
COPY gateway.py ./gateway.py
RUN groupadd -g 10001 msw && useradd -u 10001 -g msw -M msw && chmod -R a-w /opt/msw
USER 10001:10001
EXPOSE 10000
CMD ["python3","/opt/msw/gateway.py"]
