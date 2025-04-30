import streamlit as st
import pypdf
import io
import re
from datetime import datetime
import logging
from supabase import create_client, Client
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from urllib.parse import quote

# --- Configuration ---
MAX_DOC_SIZE_MB = 10
MAX_IMG_SIZE_MB = 5
MAX_DOC_SIZE_BYTES = MAX_DOC_SIZE_MB * 1024 * 1024
MAX_IMG_SIZE_BYTES = MAX_IMG_SIZE_MB * 1024 * 1024
PHONE_REGEX = r'^\d{10}$'
SHOP_NUMBER = st.secrets["shop_number"]
FOLDER_ID = st.secrets["folder_id"]
SUPABASE_URL = st.secrets["supabase_url"]
SUPABASE_KEY = st.secrets["supabase_key"]
COLOR_PRICE_PER_SIDE = 5.0
BW_PRICE_PER_SIDE = 2.0

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global Supabase Client ---
supabase = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("Supabase initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Supabase: {str(e)}")
    st.error(f"Failed to connect to database: {str(e)}")

# --- Helper Functions ---
def validate_phone(phone):
    """Checks if the phone number is exactly 10 digits."""
    return bool(phone and re.match(PHONE_REGEX, phone))

def get_pdf_page_count(file_content):
    """Reads the PDF file content and returns the page count."""
    if not file_content:
        logger.warning("get_pdf_page_count called with empty content.")
        return 0
    try:
        pdf_reader = pypdf.PdfReader(io.BytesIO(file_content))
        count = len(pdf_reader.pages)
        logger.info(f"Read {count} pages from PDF.")
        return count
    except Exception as e:
        st.error(f"Error reading PDF: The file might be corrupted or password-protected. {str(e)}")
        logger.error(f"PDF read error: {str(e)}")
        return 0

def calculate_price(page_count, copies, is_color, print_layout):
    """Calculates the printing price for a single file."""
    if page_count <= 0 or copies <= 0:
        return 0.0
    base_price_per_side = COLOR_PRICE_PER_SIDE if is_color else BW_PRICE_PER_SIDE
    price_multiplier = 0.5 if print_layout == "Double-sided" else 1.0
    return page_count * base_price_per_side * price_multiplier * copies

def upload_to_drive(file_content, file_name, folder_id):
    """Uploads file content to a specific Google Drive folder."""
    try:
        creds = Credentials(
            token=None,
            refresh_token=st.secrets["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=st.secrets["client_id"],
            client_secret=st.secrets["client_secret"],
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        drive_service = build('drive', 'v3', credentials=creds)
        logger.info(f"Uploading '{file_name}' to Drive folder '{folder_id}'")
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream')
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        logger.info(f"File '{file_name}' uploaded successfully with ID: {file.get('id')}")
        return file.get('webViewLink')
    except Exception as e:
        logger.error(f"Error uploading '{file_name}' to Drive: {str(e)}")
        st.error(f"Failed to upload {file_name} to Google Drive.")
        return None

def save_request(phone, documents, screenshot_link):
    """Save Pickup request metadata to Supabase."""
    if not supabase:
        st.error("Database connection unavailable.")
        return None
    try:
        data = {
            "phone": phone,
            "screenshot_link": screenshot_link,
            "documents": documents,  # Array of documents with preferences
            "submitted_at": datetime.now().isoformat(),
            "status": "Pending"
        }
        response = supabase.table("print_requests").insert(data).execute()
        request_id = response.data[0]["id"]
        logger.info(f"Saved Pickup request for phone {phone}. ID: {request_id}")
        return request_id
    except Exception as e:
        logger.error(f"Failed to save request: {str(e)}")
        st.error(f"Failed to save request: {str(e)}")
        return None

# --- Streamlit App UI ---
st.set_page_config(page_title="PrintEasy", layout="centered")
st.title("PrintEasy Service")
st.markdown("Upload your documents and payment proof.")

# Initialize session state
if 'files_data' not in st.session_state:
    st.session_state.files_data = []  # List of dicts: {name, content, page_count, preferences, doc_link}
if 'total_price' not in st.session_state:
    st.session_state.total_price = 0.0
if 'ss_link' not in st.session_state:
    st.session_state.ss_link = None
if 'ss_content' not in st.session_state:
    st.session_state.ss_content = None

# Stop if Supabase failed to initialize
if not supabase:
    st.stop()

# --- Step 1: Upload Documents ---
st.subheader("1. Upload Documents")
st.markdown("Upload one or more PDFs and specify printing preferences for each.")

# File uploader for multiple PDFs
uploaded_files = st.file_uploader(
    f"Upload Documents (PDF only, max {MAX_DOC_SIZE_MB}MB per file)",
    type=["pdf"],
    accept_multiple_files=True,
    key="doc_uploader"
)

# Process uploaded files
if uploaded_files:
    # Update files_data with new uploads
    new_files = []
    existing_names = {file_data['name'] for file_data in st.session_state.files_data}
    
    for uploaded_file in uploaded_files:
        if uploaded_file.name not in existing_names:
            content = uploaded_file.getvalue()
            if len(content) > MAX_DOC_SIZE_BYTES:
                st.error(f"Document '{uploaded_file.name}' exceeds {MAX_DOC_SIZE_MB}MB limit.")
                continue
            page_count = get_pdf_page_count(content)
            if page_count == 0:
                st.error(f"Could not read page count for '{uploaded_file.name}'.")
                continue
            new_files.append({
                'name': uploaded_file.name,
                'content': content,
                'page_count': page_count,
                'preferences': {
                    'copies': 1,
                    'is_color': False,
                    'print_layout': "Single-sided",
                    'pages_per_sheet': "1 page per side",
                    'page_preference': "All Pages",
                    'custom_pages': ""
                },
                'doc_link': None
            })
    
    # Append new files to session state
    st.session_state.files_data.extend(new_files)

# Display uploaded files with preferences
if st.session_state.files_data:
    st.markdown("### Uploaded Documents")
    total_price = 0.0
    for idx, file_data in enumerate(st.session_state.files_data):
        with st.expander(f"Document {idx + 1}: {file_data['name']} ({file_data['page_count']} pages)"):
            # Printing preferences for each file
            col1, col2 = st.columns(2)
            with col1:
                is_color = st.radio(
                    "Print Mode",
                    (f"⚫ Black & White (₹{BW_PRICE_PER_SIDE:.2f}/side)", f"🌈 Color (₹{COLOR_PRICE_PER_SIDE:.2f}/side)"),
                    key=f"print_mode_{idx}",
                    horizontal=True
                )
                file_data['preferences']['is_color'] = is_color.startswith("🌈")
            with col2:
                copies = st.number_input(
                    "Number of Copies",
                    min_value=1,
                    max_value=20,
                    value=file_data['preferences']['copies'],
                    step=1,
                    key=f"copies_input_{idx}"
                )
                file_data['preferences']['copies'] = copies

            print_layout = st.radio(
                "Print Layout",
                ("Single-sided", "Double-sided"),
                key=f"print_layout_{idx}",
                index=0 if file_data['preferences']['print_layout'] == "Single-sided" else 1,
                horizontal=True
            )
            file_data['preferences']['print_layout'] = print_layout

            pages_per_sheet = st.selectbox(
                "Pages per Sheet Side",
                ("1 page per side", "2 pages per side"),
                key=f"pages_per_sheet_{idx}",
                index=0 if file_data['preferences']['pages_per_sheet'] == "1 page per side" else 1
            )
            file_data['preferences']['pages_per_sheet'] = pages_per_sheet

            page_preference = st.radio(
                "Page Preference",
                ("All Pages", "Custom Pages"),
                key=f"page_preference_{idx}",
                index=0 if file_data['preferences']['page_preference'] == "All Pages" else 1,
                horizontal=True
            )
            file_data['preferences']['page_preference'] = page_preference
            if page_preference == "Custom Pages":
                custom_pages = st.text_input(
                    "Enter Page Numbers (e.g., 1-3, 5, 7-10)",
                    key=f"custom_pages_input_{idx}",
                    value=file_data['preferences']['custom_pages']
                )
                file_data['preferences']['custom_pages'] = custom_pages

            # Calculate price for this file
            file_price = calculate_price(
                file_data['page_count'],
                file_data['preferences']['copies'],
                file_data['preferences']['is_color'],
                file_data['preferences']['print_layout']
            )
            total_price += file_price
            st.write(f"*Price for this document:* ₹{file_price:.2f}")

            # Upload to Google Drive if not already uploaded
            if not file_data['doc_link']:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                sanitized_name = re.sub(r'\W+', '_', file_data['name'].split('.')[0])
                doc_file_name = f"{sanitized_name}_{timestamp}.pdf"
                doc_link = upload_to_drive(file_data['content'], doc_file_name, FOLDER_ID)
                if doc_link:
                    file_data['doc_link'] = doc_link
                    st.success(f"✅ File uploaded: [View Document]({doc_link})")
                else:
                    st.error(f"Failed to upload '{file_data['name']}'.")
                    st.stop()

    st.session_state.total_price = total_price
    st.markdown("### Total Estimated Price")
    st.write(f"*Total Price for All Documents:* ₹{total_price:.2f}")
else:
    st.warning("Please upload at least one document.")
    st.stop()

# --- Step 2: Enter Phone Number ---
st.subheader("2. Your Details")
phone = st.text_input(
    "Phone Number (10 digits)",
    max_chars=10,
    key="phone_input"
)
if not validate_phone(phone):
    st.warning("⚠ Please enter a valid 10-digit phone number.")
    st.stop()

# --- Step 3: Request Type ---
st.subheader("3. Request Type")
request_type = st.radio(
    "Request Type",
    ("Urgent (Send via WhatsApp)", "Pickup (Send to Admin Panel)"),
    key="request_type",
    horizontal=True
)

# --- Step 4: Upload Payment Proof ---
st.subheader("4. Upload Payment Proof")
payment_screenshot = st.file_uploader(
    f"Upload Payment Screenshot (JPG/PNG, max {MAX_IMG_SIZE_MB}MB)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=False,
    key="ss_uploader"
)

if payment_screenshot:
    ss_content = payment_screenshot.getvalue()
    if len(ss_content) > MAX_IMG_SIZE_BYTES:
        st.error(f"Screenshot exceeds {MAX_IMG_SIZE_MB}MB limit.")
        st.stop()
    st.session_state.ss_content = ss_content

    if not st.session_state.ss_link:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_name = re.sub(r'\W+', '_', payment_screenshot.name.split('.')[0])
        ss_file_name = f"{sanitized_name}_{timestamp}.jpg"
        ss_link = upload_to_drive(ss_content, ss_file_name, FOLDER_ID)
        if ss_link:
            st.session_state.ss_link = ss_link
            st.success(f"✅ Payment screenshot uploaded: [View Screenshot]({ss_link})")
        else:
            st.stop()

# --- Step 5: Submit Form ---
if st.button("Send Print Request"):
    if not (all(file_data['doc_link'] for file_data in st.session_state.files_data) and st.session_state.ss_link and validate_phone(phone)):
        st.error("❌ Missing required information.")
        st.stop()

    # Prepare documents data for Supabase
    documents = [
        {
            "doc_link": file_data['doc_link'],
            "pages": file_data['page_count'],
            "copies": file_data['preferences']['copies'],
            "is_color": file_data['preferences']['is_color'],
            "layout": file_data['preferences']['print_layout'],
            "pages_per_sheet": file_data['preferences']['pages_per_sheet'],
            "page_selection": file_data['preferences']['page_preference'] + (f" ({file_data['preferences']['custom_pages']})" if file_data['preferences']['page_preference'] == "Custom Pages" else ""),
            "price": calculate_price(
                file_data['page_count'],
                file_data['preferences']['copies'],
                file_data['preferences']['is_color'],
                file_data['preferences']['print_layout']
            )
        }
        for file_data in st.session_state.files_data
    ]

    # Handle different request types
    if request_type == "Urgent (Send via WhatsApp)":
        message_lines = [
            "New Urgent Print Request",
            f"Phone: {phone}",
            f"Payment Proof Link: {st.session_state.ss_link}",
            f"Payment Status: Paid (Screenshot Uploaded)",
            "--- Documents ---"
        ]
        for idx, doc in enumerate(documents, 1):
            message_lines.extend([
                f"Document {idx}:",
                f"Link: {doc['doc_link']}",
                f"Total Pages: {doc['pages']}",
                f"Copies: {doc['copies']}",
                f"Mode: {'Color' if doc['is_color'] else 'Black & White'}",
                f"Layout: {doc['layout']}",
                f"Pages per Sheet: {doc['pages_per_sheet']}",
                f"Page Selection: {doc['page_selection']}",
                f"Price: ₹{doc['price']:.2f}",
                "---------"
            ])
        message_lines.extend([
            f"Total Price: ₹{st.session_state.total_price:.2f}",
            "---",
            "Please confirm the order details."
        ])
        message = "\n".join(message_lines)
        encoded_message = quote(message)
        whatsapp_url = f"https://wa.me/{SHOP_NUMBER}?text={encoded_message}"

        st.markdown(
            f"""
            ### Your request is ready!
            Click the button below to send the details to the shopkeeper via WhatsApp.
            <a href="{whatsapp_url}" target="_blank" style="background-color: #25D366; color: white; padding: 10px 20px; text-align: center; text-decoration: none; display: inline-block; border-radius: 5px; font-weight: bold;">
                Send Details via WhatsApp
            </a>
            """,
            unsafe_allow_html=True
        )

    elif request_type == "Pickup (Send to Admin Panel)":
        request_id = save_request(phone, documents, st.session_state.ss_link)
        if request_id:
            st.success(f"✅ Success! Pickup request submitted to admin panel (ID: {request_id}).")
            # Reset session state
            st.session_state.files_data = []
            st.session_state.total_price = 0.0
            st.session_state.ss_link = None
            st.session_state.ss_content = None
        else:
            st.error("Failed to save request to database.")

# Footer
st.markdown("---")
st.caption("PrintEasy | Convenient Printing Service")