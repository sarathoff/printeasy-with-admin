import streamlit as st
import sqlite3
import pandas as pd
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configure Streamlit page
st.set_page_config(page_title="PrintEasy Admin Panel", layout="wide")

# Function to clean completed requests older than 12 hours
def clean_old_completed_requests():
    try:
        conn = sqlite3.connect('requests.db')
        c = conn.cursor()
        # Calculate cutoff time (12 hours ago)
        cutoff_time = (datetime.now() - timedelta(hours=12)).isoformat()
        # Fetch completed requests older than cutoff
        c.execute("SELECT id, submitted_at FROM print_requests WHERE status = 'Done' AND submitted_at < ?", (cutoff_time,))
        old_requests = c.fetchall()
        # Delete old requests
        for request_id, submitted_at in old_requests:
            c.execute("DELETE FROM print_requests WHERE id = ?", (request_id,))
            logger.info(f"Deleted completed request ID {request_id} submitted at {submitted_at}")
        conn.commit()
        logger.info(f"Cleaned {len(old_requests)} completed requests older than 12 hours")
    except Exception as e:
        logger.error(f"Failed to clean old completed requests: {str(e)}")
        st.error(f"Database cleanup error: {str(e)}")
    finally:
        conn.close()

# Function to mark request as done
def mark_as_done(request_id):
    try:
        conn = sqlite3.connect('requests.db')
        c = conn.cursor()
        c.execute("UPDATE print_requests SET status = 'Done' WHERE id = ?", (request_id,))
        conn.commit()
        logger.info(f"Marked request {request_id} as Done")
    except Exception as e:
        logger.error(f"Failed to mark request {request_id} as Done: {str(e)}")
        st.error(f"Database error: {str(e)}")
    finally:
        conn.close()

# Password protection
def check_password():
    if 'authenticated' not in st.session_state:
        st.session_state['authenticated'] = False
    if not st.session_state['authenticated']:
        password = st.text_input("Enter Admin Password", type="password")
        if st.button("Login"):
            if password == st.secrets["admin_password"]:
                st.session_state['authenticated'] = True
                st.rerun()
            else:
                st.error("Incorrect password")
                logger.error("Incorrect admin password entered")
        return False
    return True

# Validate secrets
if "admin_password" not in st.secrets:
    logger.error("Missing admin_password in secrets.toml")
    st.error("Configuration error: Missing admin_password in secrets.toml")
    st.stop()

# Streamlit app
st.title("PrintEasy Admin Panel (Pickup Requests)")

if not check_password():
    st.stop()

# Clean old completed requests
clean_old_completed_requests()

# Fetch pending and completed requests
try:
    conn = sqlite3.connect('requests.db')
    c = conn.cursor()
    c.execute("SELECT * FROM print_requests WHERE status = 'Pending'")
    pending_requests = c.fetchall()
    logger.info(f"Fetched {len(pending_requests)} pending requests")
    
    c.execute("SELECT * FROM print_requests WHERE status = 'Done'")
    completed_requests = c.fetchall()
    logger.info(f"Fetched {len(completed_requests)} completed requests")
except Exception as e:
    logger.error(f"Failed to fetch requests from SQLite: {str(e)}")
    st.error(f"Database error: {str(e)}")
finally:
    conn.close()

# Display Incomplete Section
st.write("### Incomplete Pickup Requests")
if pending_requests:
    df_pending = pd.DataFrame(pending_requests, columns=[
        'ID', 'Phone', 'Doc Link', 'Screenshot Link', 'Pages', 'Copies',
        'Color', 'Double-sided', 'Price', 'Submitted At', 'Status'
    ])
    for index, row in df_pending.iterrows():
        with st.expander(f"Request #{row['ID']} - Phone: {row['Phone']}"):
            st.write(f"**Document**: [{row['Doc Link']}]({row['Doc Link']})")
            st.write(f"**Screenshot**: [{row['Screenshot Link']}]({row['Screenshot Link']})")
            st.write(f"**Pages**: {row['Pages']}")
            st.write(f"**Copies**: {row['Copies']}")
            st.write(f"**Color**: {'Yes' if row['Color'] else 'No'}")
            st.write(f"**Double-sided**: {'Yes' if row['Double-sided'] else 'No'}")
            st.write(f"**Price**: ₹{row['Price']:.2f}")
            st.write(f"**Submitted At**: {row['Submitted At']}")
            if st.button("Mark as Done", key=f"done_{row['ID']}"):
                mark_as_done(row['ID'])
                st.success(f"Request #{row['ID']} moved to Completed!")
                st.rerun()
else:
    st.info("No incomplete pickup requests.")
    logger.info("No pending requests found")

# Display Completed Section
st.write("### Completed Pickup Requests")
if completed_requests:
    df_completed = pd.DataFrame(completed_requests, columns=[
        'ID', 'Phone', 'Doc Link', 'Screenshot Link', 'Pages', 'Copies',
        'Color', 'Double-sided', 'Price', 'Submitted At', 'Status'
    ])
    for index, row in df_completed.iterrows():
        with st.expander(f"Request #{row['ID']} - Phone: {row['Phone']}"):
            st.write(f"**Document**: [{row['Doc Link']}]({row['Doc Link']})")
            st.write(f"**Screenshot**: [{row['Screenshot Link']}]({row['Screenshot Link']})")
            st.write(f"**Pages**: {row['Pages']}")
            st.write(f"**Copies**: {row['Copies']}")
            st.write(f"**Color**: {'Yes' if row['Color'] else 'No'}")
            st.write(f"**Double-sided**: {'Yes' if row['Double-sided'] else 'No'}")
            st.write(f"**Price**: ₹{row['Price']:.2f}")
            st.write(f"**Submitted At**: {row['Submitted At']}")
else:
    st.info("No completed pickup requests.")
    logger.info("No completed requests found")