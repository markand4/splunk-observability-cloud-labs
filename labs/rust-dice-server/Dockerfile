FROM rust:1.77-slim AS builder
WORKDIR /app
COPY Cargo.toml .
COPY src/ src/
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/dice_server /usr/local/bin/dice_server
EXPOSE 8080
CMD ["dice_server"]
