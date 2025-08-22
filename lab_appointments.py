from flask import Blueprint, request, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
import json, os, logging
from datetime import datetime, timedelta
from rapidfuzz import process, fuzz
import dateparser
from model import summarize, is_bye, extract_date, extract_time, is_confirm, detect_language
from sms import send_sms
import psycopg2

# Blueprint for lab appointments
bp_lab = Blueprint('lab_appointments', __name__)

# --- Lab test appointment helper functions and data ---
with open('lab_tests.json', 'r', encoding='utf-8') as f:
    LAB_TESTS_LIST = json.load(f)["tests"]

def get_lab_test_names():
    return sorted(set(test["name"] for test in LAB_TESTS_LIST))

def get_lab_test_by_name(name):
    for test in LAB_TESTS_LIST:
        if test["name"].lower() == name.lower():
            return test
    return None

def get_available_lab_test_timings(name):
    test = get_lab_test_by_name(name)
    if test:
        return test["timings"]
    return None

def is_home_collection_available(name):
    test = get_lab_test_by_name(name)
    if test:
        return test.get("home_sample_collection", False)
    return False

def get_lab_db_connection():
    import os
    return psycopg2.connect(
        dbname=os.environ.get('PG_DB', 'your_db'),
        user=os.environ.get('PG_USER', 'your_user'),
        password=os.environ.get('PG_PASSWORD', 'your_password'),
        host=os.environ.get('PG_HOST', 'localhost'),
        port=os.environ.get('PG_PORT', 5432)
    )

def insert_lab_booking(test_name, date, time, name, mobile, home_collection):
    conn = get_lab_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO lab_bookings (test_name, date, time, name, mobile, home_collection) VALUES (%s, %s, %s, %s, %s, %s)""",
            (test_name, date, time, name, mobile, home_collection)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

def is_lab_slot_booked(test_name, date, time):
    try:
        with open('lab_bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for booking in bookings:
                if (booking.get('test_name') == test_name and booking.get('date') == date and booking.get('time') == time):
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        conn = get_lab_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM lab_bookings WHERE test_name=%s AND date=%s AND time=%s LIMIT 1""",
            (test_name, date, time)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        if exists:
            return True
    except Exception:
        pass
    return False

def get_available_lab_test_timings(name):
    test = get_lab_test_by_name(name)
    if test:
        return test.get("timings")
    return None

# --- Lab test appointment routes ---
# NOTE: You must ensure user_sessions is imported or passed from main.py
# Replace all @app.route with @bp_lab.route
# Example:
# @bp_lab.route('/collect-lab-test', methods=['POST'])
# def collect_lab_test():
#     ...
# (Move all lab test appointment routes here, updating decorators)