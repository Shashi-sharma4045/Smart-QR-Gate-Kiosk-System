# Smart QR Gate Kiosk System
## 3-Service AWS Architecture: Lambda + EC2 + SNS

> University Hostel Outpass Management · Dark-Mode Bento Grid UI

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        BROWSER                              │
│  Student: https://<lambda-url>/student                     │
│  Guard  : https://<lambda-url>/guard                       │
└──────────────────────────┬──────────────────────────────────┘
                           │  HTML (via Lambda Function URL)
           ┌───────────────▼───────────────┐
           │  AWS LAMBDA (Python 3.12)     │  Serves HTML
           │  lambda_function.py           │  Injects EC2_API_URL
           │  Env: EC2_API_URL             │  into <script> tag
           └───────────────────────────────┘
                           │  fetch() API calls from browser
           ┌───────────────▼───────────────┐
           │  EC2 INSTANCE (Flask + SQLite) │
           │  app.py + database.py          │
           │  Port 5000 (via Security Group)│
           │  outpass.db  (local SQLite)    │
           └───────────────┬───────────────┘
                           │  boto3 SNS publish
           ┌───────────────▼───────────────┐
           │  AMAZON SNS TOPIC             │
           │  SmartGateKioskAlerts         │
           │  Subscribers: Email / SMS     │
           └───────────────────────────────┘
```

---

## Directory Structure

```
smart-qr-gate-kiosk/
├── ec2_backend/
│   ├── app.py              Flask API + CORS + SNS
│   ├── database.py         SQLite outpass.db helper + auto-schema
│   ├── requirements.txt    Flask, flask-cors, boto3, gunicorn, python-dotenv
│   ├── .env.example        AWS_REGION, SNS_TOPIC_ARN
│   └── start.sh            Gunicorn launch script
├── lambda_frontend/
│   ├── lambda_function.py  Serves HTML, injects EC2_API_URL
│   ├── student.html        Dark-Mode Bento Grid student portal
│   └── guard.html          Mobile-first guard scanner
└── README.md               ← You are here
```

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS outpasses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id       TEXT    UNIQUE NOT NULL,       -- OUT-A20405224045-<timestamp_ms>
    student_name  TEXT    NOT NULL,
    enrollment_no TEXT    NOT NULL,
    section       TEXT    NOT NULL DEFAULT 'C',
    category      TEXT    NOT NULL,
    destination   TEXT    NOT NULL,
    out_time      TEXT    NOT NULL,              -- ISO datetime string
    in_time       TEXT    NOT NULL,              -- ISO datetime string
    status        TEXT    NOT NULL DEFAULT 'approved',  -- approved | exited | returned
    exited_at     TEXT,                          -- filled on 1st guard scan
    returned_at   TEXT,                          -- filled on 2nd guard scan
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

---

## API Reference (EC2 Flask)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/health` | Health check |
| `POST` | `/api/request_outpass` | Create approved outpass, publish SNS |
| `GET`  | `/api/pass_status/<pass_id>` | Poll current status (auto-polled every 5s) |
| `POST` | `/api/scan_qr` | Guard scans QR → state transition + SNS |
| `GET`  | `/api/recent_logs` | Latest 15 passes (guard log panel) |

### Pass ID Format
`OUT-{ENROLLMENT_NO}-{UNIX_TIMESTAMP_MS}`
Example: `OUT-A20405224045-1700000000000`

### State Machine
```
  approved ──(1st guard scan)──▶ exited ──(2nd guard scan)──▶ returned  (locked)
```

### SNS Events Published
| Trigger | Subject |
|---------|---------|
| New outpass created | `New Outpass: Shashi Kant Sharma` |
| Student exits campus | `Gate Alert: Shashi Kant Sharma EXITED` |
| Student returns to hostel | `Gate Alert: Shashi Kant Sharma RETURNED` |

---

## Step-by-Step AWS Setup

### 1. Create SNS Topic

```bash
aws sns create-topic --name SmartGateKioskAlerts --region ap-south-1
# Note the returned TopicArn, e.g.:
# arn:aws:sns:ap-south-1:123456789012:SmartGateKioskAlerts

# Subscribe your email
aws sns subscribe \
  --topic-arn arn:aws:sns:ap-south-1:123456789012:SmartGateKioskAlerts \
  --protocol email \
  --notification-endpoint your@email.com \
  --region ap-south-1
# Confirm the subscription from your inbox
```

---

### 2. Launch & Configure EC2

**A. Launch Instance**
- AMI: Ubuntu 22.04 LTS
- Instance type: `t3.micro` (free tier eligible)
- Security Group inbound rules:
  - Port 22  (SSH)      – your IP
  - Port 5000 (Flask)   – `0.0.0.0/0` (or restrict to Lambda's IPs)

**B. Attach IAM Role with SNS Publish Permission**

Create an IAM policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "sns:Publish",
    "Resource": "arn:aws:sns:ap-south-1:123456789012:SmartGateKioskAlerts"
  }]
}
```
Attach this policy to an IAM role, then attach that role to your EC2 instance.
*(This avoids hardcoding AWS credentials on the server.)*

**C. Deploy the Backend**

```bash
# SSH into EC2
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>

# Install dependencies
sudo apt update && sudo apt install -y python3-pip python3-venv git

# Upload code (from your machine)
scp -i your-key.pem -r ec2_backend/ ubuntu@<EC2_PUBLIC_IP>:~/

# Back on EC2
cd ~/ec2_backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env
# Set: AWS_REGION=ap-south-1
# Set: SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:123456789012:SmartGateKioskAlerts

# Start with Gunicorn
chmod +x start.sh && ./start.sh
```

**D. (Optional) Run as a systemd service**

```ini
# /etc/systemd/system/smartgate.service
[Unit]
Description=Smart Gate Kiosk EC2 Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/ec2_backend
ExecStart=/home/ubuntu/ec2_backend/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=always
EnvironmentFile=/home/ubuntu/ec2_backend/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable smartgate && sudo systemctl start smartgate
```

---

### 3. Deploy Lambda Frontend

**A. Package the deployment zip**

```bash
cd lambda_frontend/
zip -r lambda_deploy.zip lambda_function.py student.html guard.html
```

**B. Create Lambda Function**

```bash
aws lambda create-function \
  --function-name SmartGateKioskFrontend \
  --runtime python3.12 \
  --handler lambda_function.lambda_handler \
  --role arn:aws:iam::123456789012:role/lambda-basic-execution \
  --zip-file fileb://lambda_deploy.zip \
  --region ap-south-1
```

**C. Set Environment Variable**

```bash
aws lambda update-function-configuration \
  --function-name SmartGateKioskFrontend \
  --environment "Variables={EC2_API_URL=http://<EC2_PUBLIC_IP>:5000}" \
  --region ap-south-1
```

**D. Create Lambda Function URL**

```bash
aws lambda create-function-url-config \
  --function-name SmartGateKioskFrontend \
  --auth-type NONE \
  --region ap-south-1
# Note the returned FunctionUrl, e.g.:
# https://abc123.lambda-url.ap-south-1.on.aws/
```

**E. Grant public invoke permission**

```bash
aws lambda add-permission \
  --function-name SmartGateKioskFrontend \
  --statement-id FunctionURLAllowPublicAccess \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --region ap-south-1
```

---

### 4. Test the Full Stack

| URL | Description |
|-----|-------------|
| `https://<lambda-url>/` | Student portal |
| `https://<lambda-url>/student` | Student portal (explicit) |
| `https://<lambda-url>/guard` | Guard scanner (mobile) |
| `http://<EC2-IP>:5000/health` | EC2 health check |

**Quick API test:**
```bash
# Create outpass
curl -X POST http://<EC2-IP>:5000/api/request_outpass \
  -H "Content-Type: application/json" \
  -d '{"student_name":"Shashi Kant Sharma","enrollment_no":"A20405224045","section":"C","category":"Medical","destination":"City Hospital","out_time":"2024-01-15T10:00","in_time":"2024-01-15T22:00"}'

# Check status (replace PASS_ID)
curl http://<EC2-IP>:5000/api/pass_status/OUT-A20405224045-1700000000000

# Guard scan (exit)
curl -X POST http://<EC2-IP>:5000/api/scan_qr \
  -H "Content-Type: application/json" \
  -d '{"pass_id":"OUT-A20405224045-1700000000000"}'

# Recent logs
curl http://<EC2-IP>:5000/api/recent_logs
```

---

## Local Development (without AWS)

```bash
cd ec2_backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Leave SNS_TOPIC_ARN blank in .env → SNS calls are skipped gracefully
python app.py        # → http://localhost:5000
```

Open `lambda_frontend/student.html` directly in browser (the `{{EC2_API_URL}}`
placeholder won't be replaced, so update `const API_BASE_URL` manually to
`http://localhost:5000` for local testing).

---

## Tech Stack Summary

| Layer | Service | Technology |
|-------|---------|------------|
| Frontend Hosting | AWS Lambda (Function URL) | Python 3.12 |
| Backend API | Amazon EC2 (t3.micro) | Python Flask + Gunicorn |
| Database | Local (on EC2) | SQLite `outpass.db` |
| Messaging | Amazon SNS | boto3 |
| Styling | CDN | Tailwind CSS v3 |
| QR Generation | CDN | qrcode.js v1.0.0 |
| QR Scanning | CDN | html5-qrcode v2.3.8 |
