-- App tables
CREATE TABLE IF NOT EXISTS bookings (
  id SERIAL PRIMARY KEY,
  department TEXT NOT NULL,
  doctor TEXT NOT NULL,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  name TEXT NOT NULL,
  mobile TEXT NOT NULL,
  CONSTRAINT unique_doctor_slot UNIQUE (doctor, date, time)
);

CREATE TABLE IF NOT EXISTS lab_bookings (
  id SERIAL PRIMARY KEY,
  test_name TEXT NOT NULL,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  name TEXT NOT NULL,
  mobile TEXT NOT NULL,
  home_collection BOOLEAN DEFAULT FALSE,
  CONSTRAINT unique_lab_slot UNIQUE (test_name, date, time)
);


