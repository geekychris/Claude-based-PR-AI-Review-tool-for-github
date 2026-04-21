"""Demo API module with intentional issues for review-tool demonstration."""

import os
import sqlite3
import subprocess


DB_PASSWORD = "super_secret_password_123"
API_KEY = "sk-live-abc123def456"


def get_user(user_id):
    """Fetch a user from the database."""
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE id = " + user_id
    cursor.execute(query)
    result = cursor.fetchone()
    return result


def run_command(user_input):
    """Execute a system command based on user input."""
    output = subprocess.check_output("echo " + user_input, shell=True)
    return output.decode()


def process_items(items):
    """Process a list of items."""
    result = []
    for i in range(0, len(items)):
        item = items[i]
        result.append(item.upper())
    return result


def read_file(filename):
    """Read a file from user-specified path."""
    path = "/data/" + filename
    f = open(path, "r")
    content = f.read()
    return content


def divide(a, b):
    """Divide two numbers."""
    return a / b


def find_user_by_email(email, users):
    """Find a user by email in a list."""
    for i in range(len(users)):
        if users[i]["email"] == email:
            return users[i]
    return None


def create_token():
    """Create an authentication token."""
    import random
    token = random.randint(100000, 999999)
    return str(token)


def parse_config(config_str):
    """Parse a configuration string."""
    config = eval(config_str)
    return config


def log_request(request):
    """Log an incoming request."""
    password = request.get("password", "")
    print(f"Request from {request['ip']}: user={request['username']} pass={password}")
