import streamlit as st
import PyPDF2
import io
import re
from datetime import datetime
import logging
import traceback
from supabase import create_client, Client
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image  # To display image preview
import time
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
COLOR_PRICE_PER_SIDE = 5.0  # Updated Color Price
BW_PRICE_PER_SIDE = 2.0

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Supabase Functions ---
def init_supabase():
    """Initialize Supabase client."""
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Test connection
        supabase.table("print_requests").select("*").limit(1).execute()
        logger.info("Supabase initialized successfully")
        return supabase
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {str(e)}\n{traceback.format_exc()}")
        st.error(f"Failed to connect to database: {str(e)}")
        return None

def save_request(phone, doc_link, screenshot_link, pages, copies, is_color, layout, pages_per_sheet, price):
    """Save Pickup request metadata to Supabase."""
    supabase = init_supabase()
    if not supabase:
        return None
    try:
        data = {
            "phone": phone,
            "doc_link": doc_link,
            "screenshot_link": screenshot_link,
            "pages": pages,
            "copies": copies,
            "is_color": is_color,
            "Layout": layout,
            "pages_per_sheet": pages_per_sheet,
            "price": price,
            "submitted_at": datetime.now().isoformat(),
            "status": "Pending"
        }
        logger.debug(f"Saving request: {data}")
        response = supabase.table("print_requests").insert(data).execute()
        request_id = response.data[0]["id"]
        logger.info(f"Saved Pickup request for phone {phone} to Supabase. Request ID: {request_id}")
        return request_id
    except Exception as e:
        logger.error(f"Failed to save request to Supabase: {str(e)}\n{traceback.format_exc()}")
        st.error(f"Failed to save request: {str(e)}")
        return None

# --- Helper Functions ---
def validate_phone(phone):
    """Checks if the phone number is exactly 10 digits."""
    return bool(re.match(PHONE_REGEX, phone))

def get_pdf_page_count(file_uploader):
    """Reads the uploaded PDF file and returns the page count."""
    if file_uploader is None:
        logger.warning("get_pdf_page_count called with None file_uploader.")
        return 0
    try:
        file_uploader.seek(0)
        file_bytes = file_uploader.getvalue()
        file_uploader.seek(0)
        temp_stream = io.BytesIO(file_bytes)
        pdf_reader = PyPDF2.PdfReader(temp_stream)
        count = len(pdf_reader.pages)
        logger.info(f"Successfully read {count} pages from PDF.")
        return count
    except PyPDF2.errors.PdfReadError as e:
        st.error(f"Error reading PDF: The file '{file_uploader.name}' might be corrupted or password-protected.")
        logger.error(f"PdfReadError for {file_uploader.name}: {e}", exc_info=True)
        return None
    except Exception as e:
        st.error(f"An unexpected error occurred while reading the PDF '{file_uploader.name}': {e}")
        logger.error(f"Unexpected PDF read error for {file_uploader.name}: {e}", exc_info=True)
        return None

def calculate_price(page_count, copies, is_color, print_layout):
    """Calculates the total printing price based on options."""
    if page_count is None or page_count <= 0 or copies <= 0:
        return 0.0
    base_price_per_side = COLOR_PRICE_PER_SIDE if is_color else BW_PRICE_PER_SIDE
    price_multiplier = 0.5 if print_layout == "Double-sided" else 1.0
    price_per_original_page = base_price_per_side * price_multiplier
    total_price = page_count * price_per_original_page * copies
    return total_price

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
        logger.info(f"Attempting to upload '{file_name}' to Drive folder '{folder_id}'")
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream', resumable=True)
        request = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        )
        response = None
        upload_progress = st.empty()
        upload_progress.info(f"Uploading {file_name}...")
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload {file_name}: {int(status.progress() * 100)}%")
        upload_progress.empty()
        file_id = response.get('id')
        view_link = response.get('webViewLink')
        logger.info(f"File '{file_name}' uploaded successfully with ID: {file_id}")
        permission = {'type': 'anyone', 'role': 'reader'}
        try:
            drive_service.permissions().create(fileId=file_id, body=permission).execute()
            logger.info(f"Permissions set for file ID: {file_id}")
        except Exception as perm_e:
            logger.error(f"Failed to set permissions for file {file_id}: {perm_e}", exc_info=True)
            st.warning(f"Could not set sharing permissions for {file_name}. The shopkeeper might need access.")
        return view_link
    except Exception as e:
        logger.error(f"Error uploading '{file_name}' to Drive: {e}", exc_info=True)
        st.error(f"Failed to upload {file_name} to Google Drive. Please try again later.")
        return None

# --- Streamlit App UI ---
st.set_page_config(page_title="PrintEasy", layout="centered")
st.title("PrintEasy Service")
st.markdown("Upload your document and payment proof.")

# Initialize session state
if 'page_count' not in st.session_state:
    st.session_state.page_count = 0
if 'total_price' not in st.session_state:
    st.session_state.total_price = 0.0
if 'form_submitted_successfully' not in st.session_state:
    st.session_state.form_submitted_successfully = False
if 'current_doc_name' not in st.session_state:
    st.session_state.current_doc_name = None
if 'current_ss_name' not in st.session_state:
    st.session_state.current_ss_name = None
if 'doc_link' not in st.session_state:
    st.session_state.doc_link = None
if 'ss_link' not in st.session_state:
    st.session_state.ss_link = None

# Initialize Supabase
if not init_supabase():
    st.error("Failed to initialize database. Please try again or contact support.")
    st.stop()

# --- Step 1: Upload Document ---
st.subheader("1. Upload Document")

uploaded_file = st.file_uploader(
    f"Upload Document (PDF only, max {MAX_DOC_SIZE_MB}MB)",
    type=["pdf"],
    accept_multiple_files=False,
    key="doc_uploader",
    help="Upload the document you want to print."
)

if uploaded_file:
    if uploaded_file.name != st.session_state.get('current_doc_name'):
        st.session_state.current_doc_name = uploaded_file.name
        page_count_result = get_pdf_page_count(uploaded_file)
        st.session_state.page_count = page_count_result if page_count_result is not None else 0
        logger.info(f"Processed new PDF: {uploaded_file.name}, Pages: {st.session_state.page_count}")

        # Upload the file to Google Drive
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sanitized_name = re.sub(r'\W+', '_', uploaded_file.name.split('.')[0])
            doc_file_name = f"{sanitized_name}_{timestamp}.pdf"
            uploaded_file.seek(0)
            doc_content = uploaded_file.getvalue()
            doc_link = upload_to_drive(doc_content, doc_file_name, FOLDER_ID)

            if doc_link:
                st.success(f"✅ File uploaded successfully: [View Document]({doc_link})")
                st.session_state.doc_link = doc_link
            else:
                st.error("❌ Failed to upload the document. Please try again.")
        except Exception as e:
            logger.error(f"Error uploading file {uploaded_file.name}: {e}", exc_info=True)
            st.error(f"An error occurred while uploading the file: {e}")

    # Display document details
    if st.session_state.page_count > 0:
        st.info(f"✅ Document: **{st.session_state.current_doc_name}** ({st.session_state.page_count} pages)")
    else:
        st.warning(f"⚠️ Could not read page count for {st.session_state.current_doc_name}.")
else:
    st.session_state.current_doc_name = None
    st.session_state.page_count = 0
    st.session_state.doc_link = None
    st.stop()  # Stop execution until a file is uploaded

# --- Step 2: Enter Phone Number ---
st.subheader("2. Your Details")

phone = st.text_input(
    "Phone Number (10 digits)",
    max_chars=10,
    key="phone_input",
    help="Used for file naming and communication."
)

if not validate_phone(phone):
    st.warning("⚠ Please enter a valid 10-digit phone number.")
    st.stop()

# --- Step 3: Printing Preferences ---
st.markdown("---")
st.subheader("3. Printing Preferences")

col1, col2 = st.columns(2)
with col1:
    print_mode = st.radio(
        "Print Mode",
        (f"⚫ Black & White (₹{BW_PRICE_PER_SIDE:.2f}/side)", f"🌈 Color (₹{COLOR_PRICE_PER_SIDE:.2f}/side)"),
        key="print_mode",
        horizontal=True
    )
    is_color = (print_mode == f"🌈 Color (₹{COLOR_PRICE_PER_SIDE:.2f}/side)")

with col2:
    copies = st.number_input(
        "Number of Copies", min_value=1, max_value=20, value=1, step=1, key="copies_input"
    )

print_layout = st.radio(
    "Print Layout", ("Single-sided", "Double-sided"), key="print_layout", index=0, horizontal=True
)

# Add pages per sheet option from the Supabase version
pages_per_sheet = st.selectbox(
    "Pages per Sheet Side", ("1 page per side", "2 pages per side"), key="pages_per_sheet", index=0
)

# --- New Option: Page Preference ---
page_preference = st.radio(
    "Page Preference",
    ("All Pages", "Custom Pages"),
    key="page_preference",
    horizontal=True
)

if page_preference == "Custom Pages":
    custom_pages = st.text_input(
        "Enter Page Numbers (e.g., 1-3, 5, 7-10)",
        key="custom_pages_input",
        help="Specify the pages you want to print. Use commas to separate ranges or individual pages."
    )

# --- New Option: Request Type ---
request_type = st.radio(
    "Request Type",
    ("Urgent (Send via WhatsApp)", "Pickup (Send to Admin Panel)"),
    key="request_type",
    horizontal=True
)

# --- Dynamic Price Calculation ---
if st.session_state.page_count > 0:
    st.session_state.total_price = calculate_price(
        st.session_state.page_count, copies, is_color, print_layout
    )
    st.markdown("### Estimated Price")
    st.write(f"*Document Pages:* {st.session_state.page_count}")
    st.write(f"*Estimated Total Price:* ₹{st.session_state.total_price:.2f}")
    st.info("Please review the estimated price before proceeding.")
else:
    st.warning("⚠ Could not calculate price. Please check the uploaded document.")
    st.stop()

# --- Step 4: Upload Payment Proof ---
st.markdown("---")
st.subheader("4. Upload Payment Proof")

payment_screenshot = st.file_uploader(
    f"Upload Payment Screenshot (JPG/PNG, max {MAX_IMG_SIZE_MB}MB)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=False,
    key="ss_uploader",
    help="Upload proof of your payment (e.g., UPI screenshot)."
)

if payment_screenshot:
    try:
        # Display preview
        payment_screenshot.seek(0)
        img = Image.open(payment_screenshot)
        st.image(img, caption=payment_screenshot.name, use_container_width=True)
        payment_screenshot.seek(0)
        
        # Upload the screenshot to Google Drive
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_name = re.sub(r'\W+', '_', payment_screenshot.name.split('.')[0])
        ss_file_name = f"{sanitized_name}_{timestamp}.jpg"
        payment_screenshot.seek(0)
        ss_content = payment_screenshot.getvalue()
        ss_link = upload_to_drive(ss_content, ss_file_name, FOLDER_ID)

        if ss_link:
            st.success(f"✅ Payment screenshot uploaded successfully: [View Screenshot]({ss_link})")
            st.session_state.current_ss_name = payment_screenshot.name
            st.session_state.ss_link = ss_link
        else:
            st.error("❌ Failed to upload the payment screenshot. Please try again.")
    except Exception as e:
        logger.error(f"Error uploading payment screenshot {payment_screenshot.name}: {e}", exc_info=True)
        st.error(f"An error occurred while uploading the payment screenshot: {e}")
else:
    st.warning("⚠ Please upload a payment screenshot.")
    st.stop()

# --- Step 5: Submit Form ---
submit_button = st.button("Send Print Request")

if submit_button:
    # Ensure we have all the required data
    if not (st.session_state.doc_link and st.session_state.ss_link and validate_phone(phone) and st.session_state.page_count > 0):
        st.error("❌ Missing required information. Please check all fields.")
        st.stop()
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.info("🔄 Processing your request...")
    
    # Handle different request types
    if request_type == "Urgent (Send via WhatsApp)":
        # Construct the message
        message_lines = [
            "New Urgent Print Request",
            f"Phone: {phone}",
            f"Document Link: {st.session_state.doc_link}",
            f"Payment Proof Link: {st.session_state.ss_link}",
            f"Payment Status: Paid (Screenshot Uploaded)",
            "--- Print Details ---",
            f"Total Pages: {st.session_state.page_count}",
            f"Copies: {copies}",
            f"Mode: {'Color' if is_color else 'Black & White'}",
            f"Layout: {print_layout}",
            f"Pages per Sheet: {pages_per_sheet}",
            f"Page Selection: {page_preference}" + (f" ({custom_pages})" if page_preference == "Custom Pages" else ""),
            f"Estimated Price: ₹{st.session_state.total_price:.2f}",
            "---",
            "Please confirm the order details."
        ]
        message = "\n".join(message_lines)
        encoded_message = quote(message)
        whatsapp_url = f"https://wa.me/{SHOP_NUMBER}?text={encoded_message}"

        progress_bar.progress(100)
        status_text.success("✅ Success! Files uploaded.")
        time.sleep(1)

        st.markdown(f"""
            ### Your request is ready!
            Click the button below to send the details to the shopkeeper via WhatsApp.
            <a href="{whatsapp_url}" target="_blank" style="background-color: #25D366; color: white; padding: 10px 20px; text-align: center; text-decoration: none; display: inline-block; border-radius: 5px; font-weight: bold;">
                Send Details via WhatsApp
            </a>
        """, unsafe_allow_html=True)

    elif request_type == "Pickup (Send to Admin Panel)":
        # Save to Supabase database
        custom_pages_value = custom_pages if page_preference == "Custom Pages" else "All Pages"
        request_id = save_request(
            phone, 
            st.session_state.doc_link, 
            st.session_state.ss_link, 
            st.session_state.page_count, 
            copies, 
            is_color, 
            print_layout, 
            pages_per_sheet, 
            st.session_state.total_price
        )
        
        if request_id:
            progress_bar.progress(100)
            status_text.success(f"✅ Success! Pickup request submitted to admin panel (ID: {request_id}).")
            time.sleep(1)
            st.success("Your Pickup request has been sent to the admin panel for processing.")
            logger.info(f"Pickup request saved to Supabase with ID: {request_id}")
        else:
            st.error("Failed to save request to database. Please try again.")
            logger.error("Failed to save Pickup request to Supabase")
            st.stop()

# Optional: Footer
st.markdown("---")
st.caption("PrintEasy | Convenient Printing Service")