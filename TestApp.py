import streamlit as st
import gspread
import pandas as pd
from datetime import datetime
import os
from PIL import Image, ImageOps
import base64
import io
import urllib.parse
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Safety Stock Tracker", layout="wide", initial_sidebar_state="expanded")

# --- HIDE STREAMLIT UI (GitHub Icon, Menu, and Footer) ---
hide_st_style = """
            <style>
            [data-testid="stToolbar"] {visibility: hidden !important;}
            footer {visibility: hidden !important;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

IMAGE_DIR = "images"
if not os.path.exists(IMAGE_DIR):
    os.makedirs(IMAGE_DIR)

# --- SESSION STATE & URL PARAMS ---
try:
    if hasattr(st, 'query_params') and "category" in st.query_params:
        st.session_state.selected_category = st.query_params["category"]
        st.session_state.current_page = "Inventory Update"
        st.query_params.clear() 
        st.rerun()
    elif hasattr(st, 'experimental_get_query_params'):
        params = st.experimental_get_query_params()
        if "category" in params:
            st.session_state.selected_category = params["category"][0]
            st.session_state.current_page = "Inventory Update"
            st.experimental_set_query_params()
            st.rerun()
except Exception:
    pass

# App Navigation States
if "selected_category" not in st.session_state:
    st.session_state.selected_category = None
if "current_page" not in st.session_state:
    st.session_state.current_page = "Inventory Update"
if "active_item" not in st.session_state:
    st.session_state.active_item = None

# --- DATABASE CONNECTION (CLOUD SECURE) ---
@st.cache_resource
def init_connection():
    # Detects if running on Streamlit Cloud (checking for secrets)
    if "gcp_service_account" in st.secrets:
        # Use cloud secrets vault
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
    else:
        # Use local JSON file
        gc = gspread.service_account(filename='credentials.json')
        
    return gc.open("Safety Stock").sheet1 

try:
    sh = init_connection()
except Exception as e:
    st.error(f"Connection Error: {e}")
    st.stop()
# --- HELPER FUNCTIONS ---
def safe_gspread_update(worksheet, range_string, values_list):
    """Safely updates Google Sheets, avoiding version crashes between gspread v5 and v6."""
    try:
        worksheet.update(range_string, values_list, value_input_option='USER_ENTERED')
    except TypeError:
        worksheet.update(values_list, range_string, value_input_option='USER_ENTERED')

def safe_gspread_delete_row(worksheet, row_index):
    """Safely deletes a row in Google Sheets across different gspread versions."""
    try:
        worksheet.delete_rows(row_index)
    except AttributeError:
        worksheet.delete_row(row_index)

def get_data():
    try:
        raw_data = sh.get_all_records()
    except Exception:
        return pd.DataFrame() 
        
    temp_df = pd.DataFrame(raw_data)
    
    if not temp_df.empty:
        temp_df.columns = temp_df.columns.str.strip()
        if 'PN' in temp_df.columns:
            temp_df['PN'] = temp_df['PN'].astype(str).str.lstrip("'")
        if 'Item Name' in temp_df.columns:
            temp_df['Item Name'] = temp_df['Item Name'].astype(str)
        if 'Current Qty' in temp_df.columns:
            temp_df['Current Qty'] = pd.to_numeric(temp_df['Current Qty'], errors='coerce').fillna(0)
        if 'Safety Stock Qty' in temp_df.columns:
            temp_df['Safety Stock Qty'] = pd.to_numeric(temp_df['Safety Stock Qty'], errors='coerce').fillna(0)
            
    return temp_df

def save_uploaded_file(uploaded_file, file_name):
    """Saves the uploaded file strictly named to map easily for items and categories"""
    safe_name = "".join([c for c in file_name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    ext = uploaded_file.name.split('.')[-1].lower()
    if ext not in ['png', 'jpg', 'jpeg']:
        ext = 'png'
        
    file_path = os.path.join(IMAGE_DIR, f"{safe_name}.{ext}")
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return file_path

def get_image(name, target_size=(400, 400)):
    """Returns local path if image exists, resized to a perfect square. Otherwise returns a placeholder URL"""
    safe_name = "".join([c for c in name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    for ext in ['jpg', 'png', 'jpeg']:
        path = os.path.join(IMAGE_DIR, f"{safe_name}.{ext}")
        if os.path.exists(path):
            try:
                img = Image.open(path).convert("RGB")
                img = ImageOps.fit(img, target_size, Image.Resampling.LANCZOS)
                return img
            except Exception:
                pass
    return f"https://via.placeholder.com/{target_size[0]}x{target_size[1]}.png?text=No+Photo"

def image_to_base64(img_obj):
    """Converts a PIL Image object into a base64 string for HTML rendering."""
    if isinstance(img_obj, str): 
        return img_obj
    buffered = io.BytesIO()
    img_obj.save(buffered, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()


# --- SIDEBAR NAVIGATION ---
st.sidebar.title("Navigation")
pages_list = ["Inventory Update", "Low Stock Alerts", "Master List", "Add New Item"]
page = st.sidebar.radio("Go to", pages_list, index=pages_list.index(st.session_state.current_page) if st.session_state.current_page in pages_list else 0)

if st.session_state.current_page != page:
    st.session_state.current_page = page
    st.session_state.selected_category = None
    st.session_state.active_item = None
    st.rerun()


# ==========================================
# PAGE 1: INVENTORY UPDATE (Operator Page)
# ==========================================
if page == "Inventory Update":
    
    # --- APP BRANDING HEADER WITH LOGO ---
    col_title, col_logo = st.columns([6, 1])
    
    with col_title:
        st.title("📦 Safety Stock Tracker")
        st.caption("Created by Deanie • Version 1.0.0")
        
    with col_logo:
        logo_path = None
        for ext in ['png', 'jpg', 'jpeg']:
            path = os.path.join(IMAGE_DIR, f"WALogo.{ext}")
            if os.path.exists(path):
                logo_path = path
                break
        
        if logo_path:
            st.image(logo_path, use_container_width=True)
            
    st.divider()

    df = get_data()
    
    if df.empty or 'Category' not in df.columns:
        st.info("No items available. Please add items via the 'Add New Item' page.")
    else:
        
        # --- VIEW 3: ACTIVE ITEM EDITOR ---
        if st.session_state.active_item:
            st.button("⬅️ Back", on_click=lambda: setattr(st.session_state, 'active_item', None))
            
            item_subset = df[df['Item Name'] == st.session_state.active_item]
            if not item_subset.empty:
                item_data = item_subset.iloc[0]
                current_row = int(item_subset.index[0]) + 2
                
                st.header(item_data['Item Name'])
                col_img, col_ctrl = st.columns([1, 1.2])
                
                with col_img:
                    st.image(get_image(item_data['Item Name'], target_size=(400, 400)), use_container_width=True)
                    with st.expander("📸 Update Item Photo"):
                        uploaded_img = st.file_uploader("Capture/Upload", type=['png', 'jpg', 'jpeg'], key="item_up")
                        if st.button("Save Item Photo"):
                            if uploaded_img:
                                save_uploaded_file(uploaded_img, item_data['Item Name'])
                                st.success("Photo updated!")
                                st.rerun()
                
                with col_ctrl:
                    st.write(f"**Part Number:** `{item_data['PN']}`") 
                    qty = int(item_data['Current Qty'])
                    safety = int(item_data['Safety Stock Qty'])
                    
                    st.metric("Current Stock", qty, delta=qty - safety)
                    
                    val = st.number_input("Amount", min_value=1, value=1)
                    mode = st.radio("Action", ["Take (-)", "Add (+)"], horizontal=True)
                    
                    if st.button("Confirm Transaction", type="primary", use_container_width=True):
                        new_qty = qty + val if mode == "Add (+)" else qty - val
                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        sh.update_cell(current_row, 4, new_qty)
                        sh.update_cell(current_row, 6, now)
                        
                        st.toast("Transaction complete!")
                        st.session_state.active_item = None
                        st.rerun()
                        
                    st.write("") # Spacer
                    
                    # --- DELETE ITEM FEATURE ---
                    with st.expander("🗑️ Delete Item"):
                        st.warning(f"Are you sure you want to permanently delete **{item_data['Item Name']}**? This action cannot be undone.")
                        if st.button("Yes, Delete Item", type="primary", use_container_width=True):
                            # Remove the row from Google Sheets
                            safe_gspread_delete_row(sh, current_row)
                            
                            # Clean up the photo from the local directory if it exists
                            safe_name = "".join([c for c in item_data['Item Name'] if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                            for ext in ['png', 'jpg', 'jpeg']:
                                photo_path = os.path.join(IMAGE_DIR, f"{safe_name}.{ext}")
                                if os.path.exists(photo_path):
                                    os.remove(photo_path)

                            st.success(f"Deleted {item_data['Item Name']} successfully!")
                            time.sleep(1) # Allow user to see the success message
                            st.session_state.active_item = None
                            st.rerun()
            else:
                st.warning("Item no longer exists.")
                
        # --- VIEWS 1 & 2 (Category Selection & Search) ---
        else:
            search_query = st.text_input("🔍 Search by Part Number (PN)", placeholder="Type a Part Number...")
            st.write("") 
            
            if search_query:
                st.subheader(f"Search Results for '{search_query}'")
                clean_search = search_query.replace("'", "")
                
                search_mask = df['PN'].str.contains(clean_search, case=False, na=False)
                search_results = df[search_mask]
                
                if search_results.empty:
                    st.warning("No items found matching that Part Number.")
                else:
                    for _, row in search_results.iterrows():
                        qty = int(row['Current Qty'])
                        safety = int(row['Safety Stock Qty'])
                        indicator = "  |  ⚠️ LOW STOCK" if qty < safety else ""
                        
                        if st.button(f"📝 {row['Item Name']} (P/N: {row['PN']})  —  [Category: {row['Category']}]{indicator}", use_container_width=True, key=f"search_{row['Item Name']}_{row['PN']}"):
                            st.session_state.active_item = row['Item Name']
                            st.session_state.selected_category = row['Category']
                            st.rerun()
                            
            else:
                # --- VIEW 1: SELECT CATEGORY ---
                if st.session_state.selected_category is None:
                    st.subheader("📂 Select Category")
                    categories = sorted(df['Category'].dropna().unique().tolist())
                    cols = st.columns(3)
                    for i, cat_name in enumerate(categories):
                        with cols[i % 3]:
                            img_obj = get_image(cat_name, target_size=(350, 350))
                            img_src = image_to_base64(img_obj)
                            safe_url_cat = urllib.parse.quote(cat_name)
                            
                            st.markdown(f"""
                            <a href="?category={safe_url_cat}" target="_self" style="display: flex; justify-content: center; margin-bottom: 10px; text-decoration: none;">
                                <img src="{img_src}" style="max-width: 350px; min-width: 190px; width: 100%; border-radius: 8px; object-fit: cover; box-shadow: 0 2px 4px rgba(0,0,0,0.1); cursor: pointer; transition: transform 0.2s;" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'">
                            </a>
                            """, unsafe_allow_html=True)
                            
                            if st.button(cat_name, key=f"cat_{cat_name}", use_container_width=True):
                                st.session_state.selected_category = cat_name
                                st.rerun()
        
                # --- VIEW 2: SELECT ITEM WITHIN CATEGORY ---
                elif st.session_state.selected_category and st.session_state.active_item is None:
                    st.button("⬅️ Back", on_click=lambda: setattr(st.session_state, 'selected_category', None))
                    st.subheader(f"📦 {st.session_state.selected_category}")
                    filtered_df = df[df['Category'] == st.session_state.selected_category]
                    
                    if filtered_df.empty:
                        st.warning("No items found in this category.")
                    else:
                        for _, row in filtered_df.iterrows():
                            qty = int(row['Current Qty'])
                            safety = int(row['Safety Stock Qty'])
                            indicator = "  |  ⚠️ LOW STOCK" if qty < safety else ""
                            
                            if st.button(f"{row['Item Name']} (P/N: {row['PN']}){indicator}", use_container_width=True):
                                st.session_state.active_item = row['Item Name']
                                st.rerun()
                                
                    st.divider()
                    
                    # --- QUICK ADD NEW ITEM FEATURE ---
                    with st.expander(f"➕ Add New Item to {st.session_state.selected_category}"):
                        with st.form("quick_add_form", clear_on_submit=True):
                            q_name = st.text_input("Item Name")
                            q_pn = st.text_input("Part Number (PN)")
                            qc1, qc2 = st.columns(2)
                            q_qty = qc1.number_input("Current Qty", min_value=0)
                            q_safety = qc2.number_input("Safety Stock Qty", min_value=0)
                            q_photo = st.file_uploader("Item Photo (Optional)", type=['png', 'jpg', 'jpeg'])
                            
                            if st.form_submit_button("Add Item to Category"):
                                if q_name.strip() == "" or q_pn.strip() == "":
                                    st.error("Please provide both a valid Item Name and Part Number.")
                                else:
                                    is_dup_name = False
                                    is_dup_pn = False
                                    
                                    # Strict Duplicate Verification
                                    if not df.empty and 'Item Name' in df.columns and 'PN' in df.columns:
                                        is_dup_name = (df['Item Name'].str.lower() == q_name.strip().lower()).any()
                                        is_dup_pn = (df['PN'].str.lower() == q_pn.strip().lower()).any()
                                    
                                    if is_dup_name or is_dup_pn:
                                        if is_dup_name and is_dup_pn:
                                            st.error(f"❌ Both Item Name '{q_name}' and Part Number '{q_pn}' already exist in the system!")
                                        elif is_dup_name:
                                            st.error(f"❌ Item Name '{q_name}' already exists in the system!")
                                        else:
                                            st.error(f"❌ Part Number '{q_pn}' already exists in the system!")
                                    else:
                                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        row_data = [st.session_state.selected_category, q_name.strip(), q_pn.strip(), q_qty, q_safety, now]
                                        
                                        if q_photo:
                                            save_uploaded_file(q_photo, q_name.strip()) 
                                            
                                        next_empty_row = len(sh.get_all_values()) + 1
                                        if next_empty_row == 1:
                                            safe_gspread_update(sh, "A1:F1", [["Category", "Item Name", "PN", "Current Qty", "Safety Stock Qty", "Last Modified Date"]])
                                            next_empty_row = 2
                                            
                                        safe_gspread_update(sh, f"A{next_empty_row}:F{next_empty_row}", [row_data])
                                        st.toast(f"✅ Added {q_name.strip()} successfully!")
                                        time.sleep(1) 
                                        st.rerun()


# ==========================================
# PAGE 2: LOW STOCK ALERTS
# ==========================================
elif page == "Low Stock Alerts":
    st.title("⚠️ Low Stock Alerts")
    st.caption("Items requiring immediate restocking")
    st.divider()
    
    df = get_data()
    if not df.empty and 'Current Qty' in df.columns and 'Safety Stock Qty' in df.columns:
        low_stock_df = df[df['Current Qty'] < df['Safety Stock Qty']].copy()
        
        if low_stock_df.empty:
            st.success("All items are at or above safety stock levels! 🎉")
        else:
            st.error(f"Found {len(low_stock_df)} item(s) currently below safety stock levels.")
            low_stock_df['Deficit'] = low_stock_df['Safety Stock Qty'] - low_stock_df['Current Qty']
            display_cols = ['Category', 'Item Name', 'PN', 'Current Qty', 'Safety Stock Qty', 'Deficit']
            display_df = low_stock_df[[c for c in display_cols if c in low_stock_df.columns]]
            st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.info("No data available in the system yet.")


# ==========================================
# PAGE 3: MASTER LIST
# ==========================================
elif page == "Master List":
    st.title("📋 Master List")
    df = get_data()
    if not df.empty:
        st.dataframe(df, use_container_width=True, height=600)
    else:
        st.info("No data available in the system yet.")


# ==========================================
# PAGE 4: ADD NEW ITEM
# ==========================================
elif page == "Add New Item":
    st.title("➕ Add Multiple New Items")
    st.caption("Use this page to add new categories or bulk add items into different categories.")
    
    df = get_data()
    existing_categories = []
    
    if not df.empty and 'Category' in df.columns:
        existing_categories = df['Category'].dropna().unique().tolist()
    
    if "-- NEW CATEGORY --" not in existing_categories:
        existing_categories.insert(0, "-- NEW CATEGORY --")

    # --- STANDARD ADD ITEM FORM ---
    with st.form("add_item_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            cat_choice = st.selectbox("Select Category", existing_categories)
            new_cat_name = st.text_input("Or Type New Category Name")
            new_name = st.text_input("Item Name")
            new_pn = st.text_input("Part Number (PN)")
        with col2:
            new_qty = st.number_input("Current Qty", min_value=0)
            new_safety = st.number_input("Safety Stock Qty", min_value=0)
            new_photo = st.file_uploader("Item Photo", type=['png', 'jpg', 'jpeg'])
            
        submit_btn = st.form_submit_button("Add Item to System")
        
    if submit_btn:
        final_cat = new_cat_name.strip() if new_cat_name.strip() != "" else cat_choice
        
        if final_cat == "-- NEW CATEGORY --" or new_name.strip() == "" or new_pn.strip() == "":
            st.error("Please provide a valid Category, Item Name, and Part Number.")
        else:
            is_dup_name = False
            is_dup_pn = False
            
            # Strict Duplicate Verification
            if not df.empty and 'Item Name' in df.columns and 'PN' in df.columns:
                is_dup_name = (df['Item Name'].str.lower() == new_name.strip().lower()).any()
                is_dup_pn = (df['PN'].str.lower() == new_pn.strip().lower()).any()

            if is_dup_name or is_dup_pn:
                if is_dup_name and is_dup_pn:
                    st.error(f"❌ Both Item Name '{new_name}' and Part Number '{new_pn}' already exist in the system!")
                elif is_dup_name:
                    st.error(f"❌ Item Name '{new_name}' already exists in the system!")
                else:
                    st.error(f"❌ Part Number '{new_pn}' already exists in the system!")
            else:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                row_data = [final_cat, new_name.strip(), new_pn.strip(), new_qty, new_safety, now]
                
                if new_photo:
                    save_uploaded_file(new_photo, new_name.strip()) 
                
                next_empty_row = len(sh.get_all_values()) + 1
                if next_empty_row == 1:
                    safe_gspread_update(sh, "A1:F1", [["Category", "Item Name", "PN", "Current Qty", "Safety Stock Qty", "Last Modified Date"]])
                    next_empty_row = 2
                    
                safe_gspread_update(sh, f"A{next_empty_row}:F{next_empty_row}", [row_data])
                st.success(f"✅ Added {new_name.strip()} to {final_cat} successfully!")
                st.balloons()
