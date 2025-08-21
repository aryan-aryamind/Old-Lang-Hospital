from flask import Blueprint, request , jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
import json, os, logging
from datetime import datetime, timedelta
from rapidfuzz import process, fuzz
import dateparser
from model import summarize, is_bye, extract_date, extract_time, is_confirm, detect_language
from sms import send_sms
import psycopg2

# Blueprint for doctor appointments
bp_doctor = Blueprint('doctor_appointments', __name__)

# --- Doctor appointment helper functions and data ---
with open('doctors_list.json', 'r', encoding='utf-8') as f:
    DOCTORS_LIST = json.load(f)["doctors"]

def get_departments_from_doctors_list():
    return sorted(set(doc["doctor_department"] for doc in DOCTORS_LIST))

def get_doctors_by_department_from_list(department):
    return [doc for doc in DOCTORS_LIST if doc["doctor_department"].lower() == department.lower()]

def get_department_by_doctor_name(doctor_name):
    for doc in DOCTORS_LIST:
        if doc["doctor_name"].lower() == doctor_name.lower():
            return doc["doctor_department"]
    return None

def get_valid_times_for_department(department):
    from datetime import datetime, timedelta
    valid_times = set()
    for doc in DOCTORS_LIST:
        if doc['doctor_department'].lower() == department.lower():
            start_str, end_str = doc['doctor_available_time'].replace(' ', '').split('to')
            start_dt = datetime.strptime(start_str, '%H')
            end_dt = datetime.strptime(end_str, '%H')
            t = start_dt
            while t < end_dt:
                slot_start = t.strftime('%H:%M')
                slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                valid_times.add(f"{slot_start}-{slot_end}")
                t += timedelta(minutes=30)
            lunch = doc.get('lunch_break')
            if lunch:
                lunch_start, lunch_end = lunch.replace(' ', '').split('-')
                lunch_start_dt = datetime.strptime(lunch_start, '%H:%M')
                lunch_end_dt = datetime.strptime(lunch_end, '%H:%M')
                t = lunch_start_dt
                while t < lunch_end_dt:
                    slot_start = t.strftime('%H:%M')
                    slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                    valid_times.discard(f"{slot_start}-{slot_end}")
                    t += timedelta(minutes=30)
    return sorted(valid_times)

def get_available_slots_for_department_and_date(department, date):
    from datetime import datetime, timedelta
    doctors_in_dept = [doc for doc in DOCTORS_LIST if doc['doctor_department'].lower() == department.lower()]
    booked_slots_for_date = set()
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for b in bookings:
                if b.get('date') == date:
                    booked_slots_for_date.add((b.get('doctor'), b.get('time')))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    all_available_slots = []
    for doc_info in doctors_in_dept:
        doc_name = doc_info['doctor_name']
        try:
            start_str, end_str = doc_info['doctor_available_time'].replace(' ', '').split('to')
            start_hour = int(start_str)
            if start_hour < 7: start_hour += 12
            end_hour = int(end_str)
            if end_hour <= 7: end_hour += 12
            start_dt = datetime.strptime(str(start_hour), '%H')
            end_dt = datetime.strptime(str(end_hour), '%H')
        except (ValueError, KeyError):
            continue
        lunch_start_dt, lunch_end_dt = None, None
        if lunch := doc_info.get('lunch_break'):
            try:
                lunch_start_str, lunch_end_str = lunch.replace(' ', '').split('-')
                lunch_start_dt = datetime.strptime(lunch_start_str, '%H:%M')
                lunch_end_dt = datetime.strptime(lunch_end_str, '%H:%M')
            except (ValueError, KeyError):
                pass
        current_time = start_dt
        while current_time < end_dt:
            slot_start_time = current_time
            slot_end_time = current_time + timedelta(minutes=30)
            slot_str = f"{slot_start_time.strftime('%H:%M')}-{slot_end_time.strftime('%H:%M')}"
            is_in_lunch = lunch_start_dt and lunch_start_dt <= slot_start_time < lunch_end_dt
            is_booked_json = (doc_name, slot_str) in booked_slots_for_date
            is_booked_db = is_slot_booked(doc_name, date, slot_str)
            if not is_in_lunch and not is_booked_json and not is_booked_db:
                all_available_slots.append({'doctor': doc_name, 'time': slot_str})
            current_time += timedelta(minutes=30)
    all_available_slots.sort(key=lambda x: (x['time'], x['doctor']))
    return all_available_slots

def is_slot_booked(doctor, date, time):
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for booking in bookings:
                if (booking.get('doctor') == doctor and booking.get('date') == date and booking.get('time') == time):
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM bookings WHERE doctor=%s AND date=%s AND time=%s LIMIT 1""",
            (doctor, date, time)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        if exists:
            return True
    except Exception:
        pass
    return False

def get_db_connection():
    import os
    return psycopg2.connect(
        dbname=os.environ.get('PG_DB', 'your_db'),
        user=os.environ.get('PG_USER', 'your_user'),
        password=os.environ.get('PG_PASSWORD', 'your_password'),
        host=os.environ.get('PG_HOST', 'localhost'),
        port=os.environ.get('PG_PORT', 5432)
    )

def insert_booking(department, doctor, date, time, name, mobile):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO bookings (department, doctor, date, time, name, mobile)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (department, doctor, date, time, name, mobile)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise Exception("Slot already booked")
    finally:
        cur.close()
        conn.close()

# --- Doctor appointment routes ---
# NOTE: You must ensure user_sessions is imported or passed from main.py
# Replace all @app.route with @bp_doctor.route
# Example:
# @bp_doctor.route('/collect-department', methods=['POST'])
# def collect_department():
#     ...
# (Move all doctor appointment routes here, updating decorators)