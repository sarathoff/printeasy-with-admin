import streamlit as st
from supabase import create_client
import pandas as pd
from datetime import datetime, timedelta
import logging, time  # Ensure time is imported for polling

# Configuration
ADMIN_PASSWORD = st.secrets["admin_password"]
POLL_INTERVAL = 5  # ~5-second real-time updates
CLEANUP_HOURS = 12  # Cleanup Done requests after 12 hours
SUPABASE_URL = st.secrets["supabase_url"]
SUPABASE_KEY = st.secrets["supabase_key"]
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Supabase Functions
def init_supabase():
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        supabase.table("print_requests").select("*").limit(1).execute()
        logger.info("Supabase initialized")
        return supabase
    except Exception as e:
        logger.error(f"Supabase init failed: {str(e)}")
        st.error(f"Database error: {str(e)}")
        return None

def clean_old_requests():
    supabase = init_supabase()
    if not supabase:
        return
    try:
        cutoff = (datetime.now() - timedelta(hours=CLEANUP_HOURS)).isoformat()
        response = supabase.table("print_requests").delete().eq("status", "Done").lt("submitted_at", cutoff).execute()
        deleted_count = len(response.data) if response.data else 0
        logger.info(f"Cleaned {deleted_count} old requests")
    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}")

def fetch_requests(status):
    supabase = init_supabase()
    if not supabase:
        return pd.DataFrame()
    try:
        response = supabase.table("print_requests").select("*").eq("status", status).execute()
        logger.info(f"Fetched {len(response.data)} {status} requests")
        return pd.DataFrame(response.data)
    except Exception as e:
        logger.error(f"Fetch failed: {str(e)}")
        return pd.DataFrame()

def mark_request_done(request_id):
    supabase = init_supabase()
    if not supabase:
        return False
    try:
        supabase.table("print_requests").update({"status": "Done"}).eq("id", request_id).execute()
        logger.info(f"Marked request {request_id} as Done")
        return True
    except Exception as e:
        logger.error(f"Mark done failed: {str(e)}")
        st.error(f"Failed to mark request {request_id} as Done: {str(e)}")
        return False

# Streamlit UI
st.set_page_config(page_title="PrintEasy Admin", layout="wide")
st.title("PrintEasy Admin Panel")

# Authentication
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if not st.session_state.authenticated:
    password = st.text_input("Enter Admin Password", type="password")
    if st.button("Login"):
        if password == ADMIN_PASSWORD:
            st.session_state.authenticated = True
            logger.info("Admin login successful")
            st.rerun()
        else:
            st.error("Incorrect password")
            logger.warning("Admin login failed: incorrect password")
    st.stop()

# Main Logic
clean_old_requests()
pending_df = fetch_requests("Pending")
done_df = fetch_requests("Done")

st.markdown("### Incomplete Pickup Requests")
if pending_df.empty:
    st.info("No pending requests.")
else:
    for _, row in pending_df.iterrows():
        with st.expander(f"Request ID: {row['id']} | Phone: {row['phone']}"):
            st.write(f"Pages: {row['pages']}, Copies: {row['copies']}, Price: ₹{row['price']:.2f}")
            st.markdown(f"[Document]({row['doc_link']}) | [Screenshot]({row['screenshot_link']})")
            if st.button(f"Mark as Done", key=f"done_{row['id']}"):
                if mark_request_done(row['id']):
                    st.success(f"Request ID {row['id']} marked as Done!")
                st.rerun()

st.markdown("### Completed Pickup Requests")
if done_df.empty:
    st.info("No completed requests.")
else:
    for _, row in done_df.iterrows():
        st.write(f"ID: {row['id']}, Phone: {row['phone']}, Price: ₹{row['price']:.2f}")

# Polling for Real-Time Updates
time.sleep(POLL_INTERVAL)
st.rerun()