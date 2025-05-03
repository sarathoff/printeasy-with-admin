import streamlit as st
import PyPDF2
import io
import re
import sqlite3
from datetime import datetime
import logging
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import time
from PIL import Image  # To display image preview

# --- Configuration ---
MAX_DOC_SIZE_MB = 10
MAX_IMG_SIZE_MB = 5
MAX_DOC_SIZE_BYTES = MAX_DOC_SIZE_MB * 1024 * 1024
MAX_IMG_SIZE_BYTES = MAX_IMG_SIZE_MB * 1024 * 1024
PHONE_REGEX = r'^\d{10}$'
SHOP_NUMBER = st.secrets["shop_number"]
FOLDER_ID = st.secrets["folder_id"]
COLOR_PRICE_PER_SIDE = 5.0  # Updated Color Price
BW_PRICE_PER_SIDE = 2.0

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Database Functions ---
def init_db():
    """Initialize SQLite database for Pickup requests."""
    try:
        conn = sqlite3.connect('requests.db', timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS print_requests
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      phone TEXT,
                      doc_link TEXT,
                      screenshot_link TEXT,
                      pages INTEGER,
                      copies INTEGER,
                      is_color BOOLEAN,
                      Layout TEXT,
                      pages_per_sheet TEXT,
                      price REAL,
                      submitted_at TEXT,
                      status TEXT)''')
        conn.commit()
        # Verify schema
        c.execute("PRAGMA table_info(print_requests)")
        columns = [info[1] for info in c.fetchall()]
        logger.info(f"Database initialized. Columns: {columns}")
        if 'Layout' not in columns:
            logger.error("Schema missing 'Layout' column")
            st.error("Database schema error: Missing 'Layout' column")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize SQLite database: {str(e)}")
        st.error(f"Database initialization error: {str(e)}")
    finally:
        conn.close()

def save_request(phone, doc_link, screenshot_link, pages, copies, is_color, layout, pages_per_sheet, price):
    """Save Pickup request to SQLite database."""
    try:
        conn = sqlite3.connect('requests.db', timeout=10)
        c = conn.cursor()
        c.execute('''INSERT INTO print_requests
                     (phone, doc_link, screenshot_link, pages, copies, is_color, Layout, pages_per_sheet, price, submitted_at, status)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (phone, doc_link, screenshot_link, pages, copies, is_color, layout, pages_per_sheet, price, datetime.now().isoformat(), 'Pending'))
        conn.commit()
        # Verify insertion
        c.execute("SELECT id FROM print_requests WHERE phone = ? AND submitted_at = ?", (phone, datetime.now().isoformat()))
        request_id = c.fetchone()
        logger.info(f"Saved Pickup request for phone {phone} to SQLite. Request ID: {request_id}")
        return request_id
    except sqlite3.Error as e:
        logger.error(f"Failed to save request to SQLite: {str(e)}")
        st.error(f"Database save error: {str(e)}")
        return None
    finally:
        conn.close()

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

# Initialize database
init_db()

# --- Input Form ---
with st.form("print_form", clear_on_submit=False):
    st.subheader("1. Your Details")
    phone = st.text_input("Phone Number (10 digits)", max_chars=10, key="phone_input", help="Used for file naming.")

    st.markdown("---")
    st.subheader("2. Upload Files")

    # --- Document Upload ---
    uploaded_file = st.file_uploader(
        f"Upload Document (PDF only, max {MAX_DOC_SIZE_MB}MB)",
        type=["pdf"],
        accept_multiple_files=False,
        key="doc_uploader",
        help="Upload the document you want to print."
    )
    pdf_preview_area = st.empty()
    if uploaded_file:
        if uploaded_file.name != st.session_state.get('current_doc_name'):
            st.session_state.current_doc_name = uploaded_file.name
            page_count_result = get_pdf_page_count(uploaded_file)
            st.session_state.page_count = page_count_result if page_count_result is not None else 0
            logger.info(f"Processed new PDF: {uploaded_file.name}, Pages: {st.session_state.page_count}")
        if st.session_state.page_count is not None and st.session_state.page_count > 0:
            with pdf_preview_area.container():
                st.info(f"✅ Document: **{st.session_state.current_doc_name}** ({st.session_state.page_count} pages)")
        elif st.session_state.page_count is None:
            with pdf_preview_area.container():
                st.warning(f"⚠️ Could not read page count for {st.session_state.current_doc_name}.")
    else:
        st.session_state.current_doc_name = None
        st.session_state.page_count = 0
        pdf_preview_area.empty()

    # --- Payment Screenshot Upload ---
    payment_screenshot = st.file_uploader(
        f"Upload Payment Screenshot (JPG/PNG, max {MAX_IMG_SIZE_MB}MB)",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=False,
        key="ss_uploader",
        help="Upload proof of your payment (e.g., UPI screenshot)."
    )
    img_preview_area = st.empty()
    if payment_screenshot:
        if payment_screenshot.name != st.session_state.get('current_ss_name'):
            st.session_state.current_ss_name = payment_screenshot.name
            try:
                payment_screenshot.seek(0)
                img = Image.open(payment_screenshot)
                payment_screenshot.seek(0)
                with img_preview_area.container():
                    st.info("✅ Payment Screenshot Uploaded:")
                    st.image(img, caption=payment_screenshot.name, use_container_width=True)
            except Exception as e:
                logger.error(f"Failed to create image preview for {st.session_state.current_ss_name}: {e}")
                with img_preview_area.container():
                    st.warning(f"⚠️ Could not display preview for {st.session_state.current_ss_name}.")
                    st.info("✅ Screenshot uploaded (preview unavailable).")
        elif st.session_state.current_ss_name:
            try:
                payment_screenshot.seek(0)
                img = Image.open(payment_screenshot)
                payment_screenshot.seek(0)
                with img_preview_area.container():
                    st.info("✅ Payment Screenshot Uploaded:")
                    st.image(img, caption=st.session_state.current_ss_name, use_container_width=True)
            except Exception:
                with img_preview_area.container():
                    st.info(f"✅ Screenshot uploaded: {st.session_state.current_ss_name} (preview unavailable).")
    else:
        st.session_state.current_ss_name = None
        img_preview_area.empty()

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

    pages_per_sheet = st.selectbox(
        "Pages per Sheet Side", ("1 page per side", "2 pages per side"), key="pages_per_sheet", index=0
    )

    request_type = st.radio(
        "Request Type", ("Urgent (Send via WhatsApp)", "Pickup (Send to Admin Panel)"),
        key="request_type", index=0, horizontal=True
    )

    st.markdown("---")
    st.subheader("4. Review & Submit")

    # --- Dynamic Price Calculation ---
    st.session_state.total_price = calculate_price(
        st.session_state.page_count, copies, is_color, print_layout
    )

    price_display_area = st.container()
    with price_display_area:
        if st.session_state.page_count is not None and st.session_state.page_count > 0:
            st.write(f"**Document Pages:** {st.session_state.page_count}")
            st.write(f"**Estimated Total Price:** ₹{st.session_state.total_price:.2f}")
            if not payment_screenshot:
                st.warning("Please pay this amount and upload the payment screenshot.")
            else:
                st.success("Price calculated. Please review details and submit.")
        elif uploaded_file and st.session_state.page_count is None:
            st.warning("Could not calculate price. Error reading PDF page count.")
        elif uploaded_file:
            st.info("Calculating price...")
        else:
            st.info("Upload a PDF document to see the estimated price.")

    submit_button = st.form_submit_button("Send Print Request")

# --- Post-Submission Logic ---
if submit_button:
    phone_val = st.session_state.phone_input
    copies_val = st.session_state.copies_input
    print_mode_val = st.session_state.print_mode
    is_color_val = (print_mode_val == f"🌈 Color (₹{COLOR_PRICE_PER_SIDE:.2f}/side)")
    print_layout_val = st.session_state.print_layout
    pages_per_sheet_val = st.session_state.pages_per_sheet
    request_type_val = st.session_state.request_type
    final_page_count = st.session_state.page_count
    final_total_price = st.session_state.total_price
    doc_file_obj = uploaded_file
    ss_file_obj = payment_screenshot

    # 1. Validation
    errors = []
    if not validate_phone(phone_val):
        errors.append("❌ Please enter a valid 10-digit phone number.")
    if not doc_file_obj:
        errors.append("❌ Please upload a document file (PDF).")
    elif final_page_count is None or final_page_count <= 0:
        errors.append("❌ Document has 0 pages or could not be read. Please upload a valid PDF.")
    elif doc_file_obj.size > MAX_DOC_SIZE_BYTES:
        errors.append(f"❌ Document file size exceeds {MAX_DOC_SIZE_MB}MB.")
    if not ss_file_obj:
        errors.append("❌ Please upload a payment screenshot (JPG/PNG).")
    elif ss_file_obj.size > MAX_IMG_SIZE_BYTES:
        errors.append(f"❌ Payment screenshot size exceeds {MAX_IMG_SIZE_MB}MB.")

    if errors:
        for error in errors:
            st.error(error)
        st.session_state.form_submitted_successfully = False
        st.stop()

    # 2. Processing Valid Submission
    st.session_state.form_submitted_successfully = False
    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        status_text.info("🔄 Processing your request...")
        logger.info(f"Processing {request_type_val} submission for phone: {phone_val}")

        # Generate unique filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_phone = re.sub(r'\D', '', phone_val)
        doc_file_name = f"{sanitized_phone}_{timestamp}_doc.pdf"
        screenshot_ext = ss_file_obj.name.split('.')[-1] if '.' in ss_file_obj.name else 'png'
        screenshot_file_name = f"{sanitized_phone}_{timestamp}_payment.{screenshot_ext}"

        # 3. Upload Files to Google Drive
        status_text.info("☁️ Uploading document...")
        doc_file_obj.seek(0)
        doc_content = doc_file_obj.getvalue()
        doc_link = upload_to_drive(doc_content, doc_file_name, FOLDER_ID)
        progress_bar.progress(50)

        if not doc_link:
            st.error("Failed to upload document. Please try submitting again.")
            st.stop()

        status_text.info("☁️ Uploading payment proof...")
        ss_file_obj.seek(0)
        screenshot_content = ss_file_obj.getvalue()
        screenshot_link = upload_to_drive(screenshot_content, screenshot_file_name, FOLDER_ID)
        progress_bar.progress(90)

        if not screenshot_link:
            st.error("Failed to upload payment screenshot. Please try submitting again.")
            st.stop()

        # 4. Handle Request Type
        if request_type_val == "Urgent (Send via WhatsApp)":
            # Generate WhatsApp Message
            status_text.info("Preparing details for shopkeeper...")
            message_lines = [
                "*New Urgent Print Request*",
                f"Phone: {phone_val}",
                f"Document Link: {doc_link}",
                f"Payment Proof Link: {screenshot_link}",
                f"Payment Status: Paid (Screenshot Uploaded)",
                "--- Print Details ---",
                f"Total Pages: {final_page_count}",
                f"Copies: {copies_val}",
                f"Mode: {'Color' if is_color_val else 'Black & White'}",
                f"Layout: {print_layout_val}",
                f"Pages per Sheet: {pages_per_sheet_val}",
                f"Estimated Price: ₹{final_total_price:.2f}",
                "---",
                "Please confirm the order details."
            ]
            message = "\n".join(message_lines)
            encoded_message = message.replace(' ', '%20').replace('\n', '%0A')
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
            logger.info(f"Generated WhatsApp link: {whatsapp_url}")

        elif request_type_val == "Pickup (Send to Admin Panel)":
            # Save to SQLite
            status_text.info("Saving request to admin panel...")
            request_id = save_request(
                phone_val, doc_link, screenshot_link, final_page_count, copies_val,
                is_color_val, print_layout_val, pages_per_sheet_val, final_total_price
            )
            if request_id:
                progress_bar.progress(100)
                status_text.success(f"✅ Success! Pickup request submitted to admin panel (ID: {request_id}).")
                time.sleep(1)
                st.success("Your Pickup request has been sent to the admin panel for processing.")
                logger.info("Pickup request saved to SQLite")
            else:
                st.error("Failed to save request to database. Please try again.")
                logger.error("Failed to save Pickup request")
                st.stop()

        # Mark as successful
        st.session_state.form_submitted_successfully = True

    except Exception as e:
        logger.error(f"Error processing {request_type_val} submission for {phone_val}: {e}", exc_info=True)
        st.error(f"An unexpected error occurred during submission: {e}")
        status_text.error("❌ Submission failed. Please check your files and try again.")
        progress_bar.empty()
        st.session_state.form_submitted_successfully = False

# Optional: Footer
st.markdown("---")
st.caption("PrintEasy | Convenient Printing")