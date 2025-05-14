import streamlit as st
import logging
from supabase import create_client, Client
from datetime import datetime

# --- Configuration ---
SUPABASE_URL = st.secrets["supabase_url"]
SUPABASE_KEY = st.secrets["supabase_key"]
ADMIN_PASSWORD = st.secrets["admin_password"]

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

supabase = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("Supabase initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Supabase: {str(e)}")
    st.error(f"Failed to connect to database: {str(e)}")

# --- Supabase Functions ---
def fetch_requests(status="Pending"):
    """Fetch requests from Supabase based on status."""
    if not supabase:
        st.error("Database connection unavailable.")
        return []
    try:
        response = supabase.table("print_requests").select("*").eq("status", status).order("submitted_at", desc=True).execute()
        # Ensure response.data is a list, even if the query fails
        return response.data if response.data is not None else []
    except Exception as e:
        logger.error(f"Failed to fetch requests: {str(e)}")
        st.error(f"Failed to fetch requests: {str(e)}")
        return []

def update_request_status(request_id, new_status):
    """Update the status of a request in Supabase."""
    if not supabase:
        st.error("Database connection unavailable.")
        return False
    try:
        response = supabase.table("print_requests").update({"status": new_status}).eq("id", request_id).execute()
        if response.data:
            logger.info(f"Updated request ID {request_id} to status '{new_status}'")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to update request ID {request_id}: {str(e)}")
        st.error(f"Failed to update request: {str(e)}")
        return False

# --- Streamlit App UI ---
st.set_page_config(page_title="PrintEasy Admin", layout="wide")
st.title("PrintEasy Admin Panel")

# Stop if Supabase failed to initialize
if not supabase:
    st.stop()

# --- Admin Authentication ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.subheader("Admin Login")
    password = st.text_input("Enter Admin Password", type="password")
    if st.button("Login"):
        if password == ADMIN_PASSWORD:
            st.session_state.authenticated = True
            st.success("Login successful!")
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# --- Admin Dashboard ---
st.subheader("Pending Requests")

# Fetch and display pending requests
pending_requests = fetch_requests("Pending")
if not pending_requests:
    st.info("No pending requests.")
else:
    for request in pending_requests:
        with st.expander(f"Request ID: {request['id']} | Phone: {request['phone']} | Submitted: {request['submitted_at']}"):
            # Handle screenshot_link being None
            screenshot_link = request.get('screenshot_link', 'Not provided')
            st.write(f"**Payment Screenshot:** {'[View](' + screenshot_link + ')' if screenshot_link != 'Not provided' else 'Not provided'}")
            
            # Handle documents being None or empty
            documents = request.get('documents', [])
            if not documents:
                st.write("**Documents:** No documents found.")
                continue
            
            st.write("**Documents:**")
            total_price = 0.0
            for idx, doc in enumerate(documents, 1):
                # Defensive checks for document fields
                doc_link = doc.get('doc_link', 'Not provided')
                pages = doc.get('pages', 0)
                copies = doc.get('copies', 1)
                is_color = doc.get('is_color', False)
                layout = doc.get('layout', 'Unknown')
                pages_per_sheet = doc.get('pages_per_sheet', 'Unknown')
                page_selection = doc.get('page_selection', 'All Pages')
                price = float(doc.get('price', 0.0))  # Ensure price is a float
                
                st.write(f"**Document {idx}:** {'[View](' + doc_link + ')' if doc_link != 'Not provided' else 'Not provided'}")
                st.write(f"  - Pages: {pages}")
                st.write(f"  - Copies: {copies}")
                st.write(f"  - Mode: {'Color' if is_color else 'Black & White'}")
                st.write(f"  - Layout: {layout}")
                st.write(f"  - Pages per Sheet: {pages_per_sheet}")
                st.write(f"  - Page Selection: {page_selection}")
                st.write(f"  - Price: ₹{price:.2f}")
                total_price += price
            st.write(f"**Total Price:** ₹{total_price:.2f}")
            
            # Mark as Done button
            if st.button(f"Mark as Done", key=f"done_{request['id']}"):
                if update_request_status(request['id'], "Done"):
                    st.success(f"Request ID {request['id']} marked as Done.")
                    st.rerun()

# --- Display Completed Requests ---
st.subheader("Completed Requests")
completed_requests = fetch_requests("Done")
if not completed_requests:
    st.info("No completed requests.")
else:
    for request in completed_requests:
        with st.expander(f"Request ID: {request['id']} | Phone: {request['phone']} | Submitted: {request['submitted_at']}"):
            # Handle screenshot_link being None
            screenshot_link = request.get('screenshot_link', 'Not provided')
            st.write(f"**Payment Screenshot:** {'[View](' + screenshot_link + ')' if screenshot_link != 'Not provided' else 'Not provided'}")
            
            # Handle documents being None or empty
            documents = request.get('documents', [])
            if not documents:
                st.write("**Documents:** No documents found.")
                continue
            
            st.write("**Documents:**")
            total_price = 0.0
            for idx, doc in enumerate(documents, 1):
                doc_link = doc.get('doc_link', 'Not provided')
                pages = doc.get('pages', 0)
                copies = doc.get('copies', 1)
                is_color = doc.get('is_color', False)
                layout = doc.get('layout', 'Unknown')
                pages_per_sheet = doc.get('pages_per_sheet', 'Unknown')
                page_selection = doc.get('page_selection', 'All Pages')
                price = float(doc.get('price', 0.0))
                
                st.write(f"**Document {idx}:** {'[View](' + doc_link + ')' if doc_link != 'Not provided' else 'Not provided'}")
                st.write(f"  - Pages: {pages}")
                st.write(f"  - Copies: {copies}")
                st.write(f"  - Mode: {'Color' if is_color else 'Black & White'}")
                st.write(f"  - Layout: {layout}")
                st.write(f"  - Pages per Sheet: {pages_per_sheet}")
                st.write(f"  - Page Selection: {page_selection}")
                st.write(f"  - Price: ₹{price:.2f}")
                total_price += price
            st.write(f"**Total Price:** ₹{total_price:.2f}")

# --- Logout Button ---
if st.button("Logout"):
    st.session_state.authenticated = False
    st.rerun()

# Footer
st.markdown("---")
st.caption("PrintEasy Admin | Manage Printing Requests")