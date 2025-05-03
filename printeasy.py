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

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize session state for reset
if 'reset' not in st.session_state:
    st.session_state['reset'] = False

# Function to reset the form
def reset_form():
    st.session_state['reset'] = True
    for key in list(st.session_state.keys()):
        if key != 'reset':
            del st.session_state[key]
    st.session_state['reset'] = False

# Function to validate phone number
def validate_phone(phone):
    pattern = r'^\d{10}$'
    return bool(re.match(pattern, phone))

# Function to calculate price
def calculate_price(page_count, copies, is_color, is_double_sided):
    base_price = 2 if is_color else 1
    price_per_page = base_price * (0.5 if is_double_sided else 1)
    total_price = page_count * price_per_page * copies
    return total_price

# Function to initialize SQLite database
def init_db():
    try:
        conn = sqlite3.connect('requests.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS print_requests
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      phone TEXT,
                      doc_link TEXT,
                      screenshot_link TEXT,
                      pages INTEGER,
                      copies INTEGER,
                      is_color BOOLEAN,
                      is_double_sided BOOLEAN,
                      price REAL,
                      submitted_at TEXT,
                      status TEXT)''')
        conn.commit()
        logger.info("SQLite database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite database: {str(e)}")
        st.error(f"Database error: {str(e)}")
    finally:
        conn.close()

# Function to save print request to SQLite
def save_request(phone, doc_link, screenshot_link, pages, copies, is_color, is_double_sided, price):
    try:
        conn = sqlite3.connect('requests.db')
        c = conn.cursor()
        c.execute('''INSERT INTO print_requests
                     (phone, doc_link, screenshot_link, pages, copies, is_color, is_double_sided, price, submitted_at, status)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (phone, doc_link, screenshot_link, pages, copies, is_color, is_double_sided, price, datetime.now().isoformat(), 'Pending'))
        conn.commit()
        logger.info(f"Saved Pickup request for phone {phone} to SQLite")
    except Exception as e:
        logger.error(f"Failed to save request to SQLite: {str(e)}")
        st.error(f"Database error: {str(e)}")
    finally:
        conn.close()

# Function to upload file to Google Drive
def upload_to_drive(file_content, file_name, folder_id):
    try:
        creds = Credentials(
            token=None,
            refresh_token=st.secrets["refresh_token"],
            client_id=st.secrets["client_id"],
            client_secret=st.secrets["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        drive_service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream')
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        drive_service.permissions().create(
            fileId=file.get('id'),
            body=permission
        ).execute()
        
        link = f"https://drive.google.com/file/d/{file.get('id')}/view"
        logger.info(f"Uploaded {file_name} to Drive: {link}")
        return link
    except Exception as e:
        logger.error(f"Error uploading {file_name} to Drive: {str(e)}")
        st.error(f"Failed to upload {file_name} to Google Drive: {str(e)}")
        return None

# Validate secrets
required_secrets = ["client_id", "client_secret", "refresh_token", "shop_number", "folder_id"]
for key in required_secrets:
    if key not in st.secrets:
        logger.error(f"Missing secret: {key}")
        st.error(f"Configuration error: Missing {key} in secrets.toml")
        st.stop()

# Initialize database
init_db()

# Streamlit app
st.title("PrintEasy")
st.write("Upload your document and payment screenshot to submit a print request!")

# Form
with st.form("print_form"):
    phone = st.text_input("Phone Number (10 digits)", max_chars=10)
    uploaded_file = st.file_uploader("Upload Document (PDF only, max 5MB)", type=["pdf"], accept_multiple_files=False)
    payment_screenshot = st.file_uploader("Upload Payment Screenshot (JPG/PNG, max 5MB)", type=["jpg", "png"], accept_multiple_files=False)
    is_color = st.checkbox("Color Print (₹2/page, Black & White ₹1/page)", value=False)
    is_double_sided = st.checkbox("Double-sided Print (50% discount on price per page)", value=False)
    copies = st.number_input("Number of Copies", min_value=1, max_value=10, value=1, step=1)
    request_type = st.radio("Request Type (Choose one)", ["Urgent (Send via WhatsApp)", "Pickup (Send to Admin Panel)"])
    submit_button = st.form_submit_button("Submit Print Request")
    reset_button = st.form_submit_button("Reset")

    if reset_button:
        reset_form()
        st.rerun()

    if submit_button:
        logger.debug("Form submitted, validating inputs")
        # Validate inputs
        if not validate_phone(phone):
            st.error("Please enter a valid 10-digit phone number.")
            logger.error("Invalid phone number")
        elif not uploaded_file:
            st.error("Please upload a document file.")
            logger.error("No document uploaded")
        elif not payment_screenshot:
            st.error("Please upload a payment screenshot.")
            logger.error("No payment screenshot uploaded")
        elif uploaded_file.size == 0:
            st.error("Cannot upload an empty document file.")
            logger.error("Empty document file")
        elif payment_screenshot.size == 0:
            st.error("Cannot upload an empty payment screenshot.")
            logger.error("Empty payment screenshot")
        elif uploaded_file.size > 5 * 1024 * 1024:
            st.error("Document file size exceeds 5MB.")
            logger.error("Document file too large")
        elif payment_screenshot.size > 5 * 1024 * 1024:
            st.error("Payment screenshot size exceeds 5MB.")
            logger.error("Screenshot file too large")
        else:
            try:
                # Read PDF and calculate pages
                pdf_reader = PyPDF2.PdfReader(uploaded_file)
                page_count = len(pdf_reader.pages)
                logger.info(f"PDF processed with {page_count} pages")

                # Calculate price
                total_price = calculate_price(page_count, copies, is_color, is_double_sided)
                st.write(f"Total Pages: {page_count}")
                st.write(f"Total Price: ₹{total_price:.2f}")

                # Upload files to Google Drive
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                doc_file_name = f"{phone}_{timestamp}_document.pdf"
                screenshot_file_name = f"{phone}_{timestamp}_screenshot.{payment_screenshot.name.split('.')[-1]}"
                
                folder_id = st.secrets["folder_id"]
                
                # Upload document
                doc_link = upload_to_drive(uploaded_file.getvalue(), doc_file_name, folder_id)
                if not doc_link:
                    logger.error("Document upload failed, stopping")
                    st.stop()
                
                # Upload screenshot
                screenshot_link = upload_to_drive(payment_screenshot.getvalue(), screenshot_file_name, folder_id)
                if not screenshot_link:
                    logger.error("Screenshot upload failed, stopping")
                    st.stop()

                # Handle request type
                if request_type == "Urgent (Send via WhatsApp)":
                    # Send via WhatsApp
                    message = (
                        f"New Urgent Print Request\n"
                        f"Phone: {phone}\n"
                        f"Document: {doc_link}\n"
                        f"Screenshot: {screenshot_link}\n"
                        f"Pages: {page_count}\n"
                        f"Copies: {copies}\n"
                        f"Color: {'Yes' if is_color else 'No'}\n"
                        f"Double-sided: {'Yes' if is_double_sided else 'No'}\n"
                        f"Total Price: ₹{total_price:.2f}"
                    )
                    encoded_message = message.replace(' ', '%20').replace('\n', '%0A')
                    whatsapp_url = f"https://wa.me/{st.secrets['shop_number']}?text={encoded_message}"
                    st.success("Urgent request submitted successfully!")
                    st.markdown(f"[Click here to send details to shopkeeper via WhatsApp]({whatsapp_url})")
                    logger.info(f"Generated WhatsApp link: {whatsapp_url}")
                elif request_type == "Pickup (Send to Admin Panel)":
                    # Save to SQLite for admin panel
                    save_request(phone, doc_link, screenshot_link, page_count, copies, is_color, is_double_sided, total_price)
                    st.success("Pickup request submitted to admin panel successfully!")
                    logger.info("Pickup request saved to SQLite")

                # Reset form after submission
                reset_form()
                
            except Exception as e:
                logger.error(f"Error processing request: {str(e)}")
                st.error(f"An error occurred: {str(e)}")