mkdir -p artifacts/logs

nohup ngrok http 8610 \
  --url https://flounder-sphere-dab.ngrok-free.dev \
  > artifacts/logs/ngrok.log 2>&1 &

echo $! > artifacts/logs/ngrok.pid

# kill "$(cat artifacts/logs/ngrok.pid)"
# rm -f artifacts/logs/ngrok.pid