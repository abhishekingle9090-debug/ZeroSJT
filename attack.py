import requests

url = "http://127.0.0.1:8000/submit"
payload = {
  "problem_id": "two-sum",
  "user_id": "abhi_123",
  "language_id": 71,
  "source_code": "print('Spamming the server!')"
}

# Fire 10 rapid-fire requests
for i in range(10):
    response = requests.post(url, json=payload)
    
    if response.status_code == 200:
        print(f"Click {i+1}: ✅ Success (200 OK)")
    elif response.status_code == 429:
        print(f"Click {i+1}: 🛑 BLOCKED (429 Rate Limit)")