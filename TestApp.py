import streamlit as st
import gspread
import pandas as pd
import numpy as np
from datetime import datetime
import os
from PIL import Image, ImageOps
import base64
import io
import urllib.parse
import time
import re

# --- 1. CONFIGURATION & STATE ---
st.set_page_config(page_title="Maintenance Inventory", layout="wide", initial_sidebar_state="expanded")

IMAGE_DIR = "images"
os.makedirs(IMAGE_DIR, exist_ok=True)

# Default session states
for key, default in [("selected_category", None), ("active_item", None), ("cat_change_msg", None)]:
    if key not in st.session_state: st.session_state[key] = default

# Seamless URL Parameter Routing
if "category" in st.query_params:
    st.session_state.selected_category = urllib.parse.unquote(st.query_params["category"])
    st.query_params.clear()
    st.rerun()

if "item" in st.query_params:
    st.session_state.active_item = int(st.query_params["item"])
    st.query_params.clear()
    st.rerun()

# --- 2. DATABASE CONNECTION ---
@st.cache_resource
def init_connection():
    try:
        gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    except (FileNotFoundError, KeyError):
        gc = gspread.service_account(filename='credentials.json')
    return gc.open("Safety Stock").sheet1 

try: sh = init_connection()
except Exception as e: st.error(f"Connection Error: {e}"); st.stop()


# --- 3. CACHED DATA FETCHING ---
@st.cache_data(ttl=600, show_spinner=False)
def get_data():
    try: raw_data = sh.get_all_values()
    except Exception: return pd.DataFrame() 
    
    if len(raw_data) < 2: return pd.DataFrame()
        
    headers = [str(h).strip() for h in raw_data[0]]
    if len(headers) < 14: headers += [f"Unnamed_{i}" for i in range(len(headers), 14)]
    if headers[13] in ["", "Unnamed_13"]: headers[13] = "Image URL"
    if "Image URL" not in headers: headers.append("Image URL")
        
    rows = [r + [""] * (len(headers) - len(r)) for r in raw_data[1:]]
    df = pd.DataFrame([r[:len(headers)] for r in rows], columns=headers)
    
    if not df.empty:
        df['Original_Row'] = df.index + 2
        if 'Category' in df.columns and 'Item Name' in df.columns:
            df[['Category', 'Item Name']] = df[['Category', 'Item Name']].replace(r'^\s*$', np.nan, regex=True)
            df = df.dropna(subset=['Category', 'Item Name'], how='all')
            df['Item Name'] = df['Item Name'].fillna("Add Name").astype(str)
            df['Category'] = df['Category'].fillna("No Category")
        if 'PN' in df.columns: df['PN'] = df['PN'].astype(str).str.lstrip("'")
        for col in ['Current Qty', 'Safety Stock Qty']:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df

def clear_image_caches():
    get_cached_image_b64.clear()

# --- GOOGLE SHEETS WRITE FUNCTIONS ---
def safe_gspread_update(worksheet, range_string, values_list):
    try: worksheet.update(values=values_list, range_name=range_string, value_input_option='USER_ENTERED')
    except TypeError: worksheet.update(range_string, values_list, value_input_option='USER_ENTERED')
    except Exception as e: st.error(f"GSpread Error: {e}")
    get_data.clear() 

def safe_gspread_delete_row(worksheet, row_index):
    try: worksheet.delete_rows(row_index)
    except AttributeError: worksheet.delete_row(row_index)
    get_data.clear() 

def safe_update_cell(worksheet, row_index, col_index, value):
    try: worksheet.update_cell(row_index, col_index, value)
    except Exception as e: st.error(f"GSpread Error: {e}")
    get_data.clear() 


# --- IMAGE PROCESSING & CACHING ---
def clean_name(name): 
    return "".join(c for c in str(name) if c.isalnum() or c.isspace()).rstrip()

def resolve_url(url):
    url = str(url).strip()
    if not url: return ""
    if "drive.google.com/file/d/" in url:
        match = re.search(r'file/d/([a-zA-Z0-9_-]+)', url)
        if match: return f"https://drive.google.com/uc?id={match.group(1)}"
    elif "drive.google.com/open?id=" in url: return url.replace("open?id=", "uc?id=")
    return url

def image_to_base64(img_obj):
    if isinstance(img_obj, str): return img_obj 
    buffered = io.BytesIO()
    img_obj.save(buffered, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()

def get_item_image(item_name, image_url="", target_size=(400, 400)):
    resolved_url = resolve_url(image_url)
    if resolved_url and resolved_url.lower() != 'nan': return resolved_url
        
    for ext in ['jpg', 'png', 'jpeg']:
        path = os.path.join(IMAGE_DIR, f"{clean_name(item_name)}.{ext}")
        if os.path.exists(path):
            try: return ImageOps.fit(Image.open(path).convert("RGB"), target_size, Image.Resampling.LANCZOS)
            except Exception: pass
    return f"https://via.placeholder.com/{target_size[0]}x{target_size[1]}.png?text=No+Photo"

@st.cache_data(show_spinner=False, max_entries=500)
def get_cached_image_b64(name, url, target_size=(400, 400)):
    return image_to_base64(get_item_image(name, url, target_size))

def has_image(name, url):
    if url and str(url).lower() != 'nan': return True
    for ext in ['jpg', 'png', 'jpeg']:
        if os.path.exists(os.path.join(IMAGE_DIR, f"{clean_name(name)}.{ext}")): return True
    return False

def save_uploaded_file(uploaded_file, file_name):
    ext = uploaded_file.name.split('.')[-1].lower()
    ext = ext if ext in ['png', 'jpg', 'jpeg'] else 'png'
    path = os.path.join(IMAGE_DIR, f"{clean_name(file_name)}.{ext}")
    with open(path, "wb") as f: f.write(uploaded_file.getbuffer())
    clear_image_caches()

def delete_image(name):
    for ext in ['png', 'jpg', 'jpeg']:
        path = os.path.join(IMAGE_DIR, f"{clean_name(name)}.{ext}")
        if os.path.exists(path): os.remove(path)
    clear_image_caches()

def rename_image(old_name, new_name):
    for ext in ['png', 'jpg', 'jpeg']:
        old_path = os.path.join(IMAGE_DIR, f"{clean_name(old_name)}.{ext}")
        if os.path.exists(old_path): os.rename(old_path, os.path.join(IMAGE_DIR, f"{clean_name(new_name)}.{ext}"))
    clear_image_caches()


# --- THEMED HTML/CSS GRID BUILDER ---
GRID_CSS = """
<style>
.inv-grid {
    display: grid;
    /* minmax(0, 1fr) ensures columns NEVER stretch past their equal fraction */
    grid-template-columns: repeat(3, minmax(0, 1fr)); 
    gap: 12px;
    margin-bottom: 20px;
    width: 100%;
}
.inv-grid.items { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 600px) { .inv-grid, .inv-grid.items { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (min-width: 900px) { .inv-grid, .inv-grid.items { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (min-width: 1200px){ .inv-grid, .inv-grid.items { grid-template-columns: repeat(5, minmax(0, 1fr)); } }

.inv-card {
    background-color: #FFFFFF; 
    border: 2px solid #C9E6A1; 
    border-radius: 12px;
    padding: 10px;
    text-align: center;
    text-decoration: none;
    color: #2C3A21 !important; 
    transition: all 0.2s ease;
    display: flex;
    flex-direction: column;
    align-items: center;
    cursor: pointer;
    box-shadow: 0 4px 6px rgba(44, 58, 33, 0.06);
    box-sizing: border-box;
    height: 100%; /* Force equal heights for rows */
}
.inv-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 16px rgba(105, 177, 19, 0.15); 
    border-color: #69B113; 
}
.inv-img {
    width: 100%;
    aspect-ratio: 1/1;
    object-fit: cover;
    border-radius: 8px;
    margin-bottom: 12px;
}
/* Force word wrapping to prevent grid stretching */
.inv-title { font-size: 0.95rem; font-weight: 700; line-height: 1.2; margin-bottom: 4px; overflow-wrap: break-word; word-wrap: break-word; hyphens: auto; width: 100%; }
.inv-subtitle { font-size: 0.75rem; color: #768744; margin-bottom: 6px; font-weight: 600; overflow-wrap: break-word; word-wrap: break-word; hyphens: auto; width: 100%; }
/* margin-top: auto pushes the status label to the bottom so cards look uniform */
.inv-status { font-size: 0.85rem; font-weight: 800; padding: 4px 8px; border-radius: 4px; margin-top: auto; }
</style>
"""

def render_category_grid_html(df):
    categories = sorted(df['Category'].dropna().unique())
    if not categories: return ""
    
    html = GRID_CSS + "<div class='inv-grid'>"
    for cat in categories:
        cat_df = df[df['Category'] == cat]
        img_b64 = None
        for _, r in cat_df.iterrows():
            url = str(r.get('Image URL', '')).strip()
            if has_image(r['Item Name'], url):
                img_b64 = get_cached_image_b64(r['Item Name'], url, (250, 250))
                break
        
        if not img_b64:
            placeholder = f"https://via.placeholder.com/250x250.png?text={urllib.parse.quote(cat)}"
            img_b64 = image_to_base64(placeholder)
            
        safe_cat = urllib.parse.quote(cat)
        html += f'<a href="?category={safe_cat}" target="_self" class="inv-card"><img src="{img_b64}" class="inv-img"><div class="inv-title">{cat}</div></a>'
    
    html += "</div>"
    return html

def render_items_grid_html(df, category=None, search_results=None):
    cat_df = search_results if search_results is not None else df[df['Category'] == category]
    if cat_df.empty: return ""
    
    html = GRID_CSS + "<div class='inv-grid items'>"
    for _, r in cat_df.iterrows():
        url = str(r.get('Image URL', '')).strip()
        img_b64 = get_cached_image_b64(r['Item Name'], url, (250, 250)) if has_image(r['Item Name'], url) else image_to_base64(f"https://via.placeholder.com/250x250.png?text={urllib.parse.quote(r['Item Name'])}")
        
        qty, safety = int(r['Current Qty']), int(r['Safety Stock Qty'])
        
        warning = "⚠️ LOW STOCK" if qty < safety else f"Stock: {qty}"
        color_style = "color: #FFA400; background-color: rgba(255, 164, 0, 0.1);" if qty < safety else "color: #768744; background-color: rgba(118, 135, 68, 0.08);"
        
        html += f'''
        <a href="?item={r["Original_Row"]}" target="_self" class="inv-card">
            <img src="{img_b64}" class="inv-img">
            <div class="inv-title">{r["Item Name"]}</div>
            <div class="inv-subtitle">P/N: {r["PN"]}</div>
            <div class="inv-status" style="{color_style}">{warning}</div>
        </a>
        '''
    html += "</div>"
    return html


# --- LOGIC HELPERS ---
def find_fuzzy_duplicates(df, target_pn, current_row_exclude=None):
    if df.empty or 'PN' not in df.columns or not str(target_pn).strip() or str(target_pn).strip().lower() == 'nan':
        return pd.DataFrame()
        
    t_pn_raw = str(target_pn).strip().lower()
    def get_tokens(text):
        for char in [',', ':', '/', '-', '(', ')', '_', '.']: text = text.replace(char, ' ')
        return set(word for word in text.split() if len(word) >= 4 and word not in {'model', 'part', 'number', 'item', 'desc'})
        
    t_tokens = get_tokens(t_pn_raw)
    
    def is_match(val):
        v = str(val).strip().lower()
        if not v or v == 'nan' or v == 'n/a': return False
        if (t_pn_raw in v) or (len(v) > 2 and v in t_pn_raw): return True
        if t_tokens.intersection(get_tokens(v)): return True
        return False
        
    duplicates = df[df['PN'].apply(is_match)]
    if current_row_exclude:
        duplicates = duplicates[duplicates['Original_Row'] != current_row_exclude]
    return duplicates

def is_valid_new_item(df, name, pn):
    if not name.strip() or not pn.strip():
        st.error("Item Name and Part Number are required."); return False
    if not df.empty and 'Item Name' in df.columns and 'PN' in df.columns:
        dup_name = (df['Item Name'].str.lower() == name.strip().lower()).any()
        dup_pn = (df['PN'].str.lower() == pn.strip().lower()).any()
        if dup_name and dup_pn: st.error(f"❌ Both Name '{name}' and PN '{pn}' exist!")
        elif dup_name: st.error(f"❌ Item Name '{name}' exists!")
        elif dup_pn: st.error(f"❌ Part Number '{pn}' exists!")
        else: return True
        return False
    return True

def append_new_item_to_sheet(row_data):
    next_row = len(sh.get_all_values()) + 1
    if next_row == 1:
        headers = [["Date", "PO Number", "Vendor", "Item No", "Category", "Item Name", "PN", "Last Qty Bought", "Unit Price", "Location", "Current Qty", "Safety Stock Qty", "Last Modified Date", "Image URL"]]
        safe_gspread_update(sh, "A1:N1", headers)
        next_row = 2
    safe_gspread_update(sh, f"A{next_row}:N{next_row}", [row_data])


# --- 4. DIALOGS (STREAMLIT MODALS) ---
@st.dialog("📸 Update Item Photo")
def update_photo_dialog(item, c_row):
    st.write("Provide an image file or a web address (URL) to save storage space.")
    img_up = st.file_uploader("Upload Image File", type=['png', 'jpg', 'jpeg'])
    st.divider()
    img_url = st.text_input("🌐 Or paste an Image URL", value=item.get('Image URL', ''), placeholder="https://example.com/image.png")
    
    if st.button("Save Item Photo", type="primary", width="stretch"):
        if img_up:
            save_uploaded_file(img_up, item['Item Name'])
            safe_update_cell(sh, c_row, 14, "") 
            st.success("Updated!"); time.sleep(0.5); st.rerun()
        elif img_url.strip():
            delete_image(item['Item Name']) 
            safe_update_cell(sh, c_row, 14, img_url.strip())
            st.success("Updated!"); time.sleep(0.5); st.rerun()
        else:
            safe_update_cell(sh, c_row, 14, "") 
            st.success("Cleared!"); time.sleep(0.5); st.rerun()

@st.dialog("✏️ Edit Item Details")
def edit_details_dialog(item, c_row):
    e_cat = st.text_input("Category", value=item['Category'])
    e_name = st.text_input("Item Name", value=item['Item Name'])
    e_pn = st.text_input("Part Number (PN)", value=item['PN'])
    
    if st.button("Save Details", type="primary", width="stretch"):
        if not e_name.strip() or not e_pn.strip(): 
            st.error("Item Name and PN are required.")
        else:
            fmt_pn = f"'{e_pn.strip()}" if e_pn.strip().isdigit() or e_pn.strip().startswith('0') else e_pn.strip()
            safe_gspread_update(sh, f"E{c_row}:G{c_row}", [[e_cat.strip(), e_name.strip(), fmt_pn]])
            safe_update_cell(sh, c_row, 13, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            
            if e_name.strip() != str(item['Item Name']).strip(): rename_image(item['Item Name'], e_name)
            if e_cat.strip() != str(item['Category']).strip(): st.session_state.cat_change_msg = f"Item is in {e_cat} category"
            
            st.toast("Details updated!")
            st.session_state.selected_category = e_cat.strip()
            time.sleep(0.5); st.rerun()

@st.dialog("🗑️ Delete Item")
def delete_item_dialog(item_name, c_row):
    st.warning(f"Are you sure you want to permanently delete **{item_name}**?")
    if st.button("Yes, Delete Item", type="primary", width="stretch"):
        safe_gspread_delete_row(sh, c_row); delete_image(item_name)
        st.success("Deleted!"); time.sleep(1); st.session_state.active_item = None; st.rerun()

@st.dialog("🔄 Merge Duplicate Items")
def merge_duplicates_dialog(item, duplicates, c_row):
    st.warning("Select the items below to merge into the current item. Their quantities will be combined, and the duplicate records will be deleted.")
    
    options = {}
    for _, r in duplicates.iterrows():
        options[r['Original_Row']] = f"{r['Item Name']} (PN: {r['PN']}) - Qty: {r['Current Qty']}"
        
    selected_rows = st.multiselect("Select duplicates to merge:", options.keys(), format_func=lambda x: options[x], default=[])
    
    if selected_rows:
        selected_df = duplicates[duplicates['Original_Row'].isin(selected_rows)]
        total_extra_qty = int(selected_df['Current Qty'].sum())
        new_total = int(item['Current Qty']) + total_extra_qty
        
        st.metric("New Total Quantity", f"{int(item['Current Qty'])} + {total_extra_qty}", f"= {new_total}")
        
        if st.button("Confirm Merge", type="primary", width="stretch"):
            safe_update_cell(sh, c_row, 11, new_total)
            safe_update_cell(sh, c_row, 13, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            
            dup_rows = sorted(selected_rows, reverse=True)
            for r in dup_rows: safe_gspread_delete_row(sh, int(r))
                
            rows_deleted_above = sum(1 for r in dup_rows if r < c_row)
            new_c_row = c_row - rows_deleted_above
            
            st.success("Successfully merged duplicates!")
            time.sleep(1); st.session_state.active_item = new_c_row; st.rerun()

@st.dialog("🔄 Similar Part Numbers Found")
def combine_item_dialog(new_data, matches, category):
    st.warning(f"We found existing items with a similar Part Number ({new_data['pn']}).")
    
    options = {"NEW": "✨ CREATE AS BRAND NEW ITEM (Do not combine)"}
    for _, r in matches.iterrows():
        options[r['Original_Row']] = f"📦 Combine with: {r['Item Name']} (PN: {r['PN']}) - Current Qty: {r['Current Qty']}"
        
    choice = st.radio("Choose action:", options.keys(), format_func=lambda x: options[x])
    
    if st.button("Confirm Action", type="primary", width="stretch"):
        if choice == "NEW":
            append_new_item_to_sheet([new_data['date'], new_data['po'], new_data['vendor'], new_data['item_no'], category, new_data['name'], new_data['pn'], new_data['lqty'], new_data['price'], new_data['location'], new_data['qty'], new_data['safe'], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), new_data['photo_url']])
            if new_data.get('photo'): save_uploaded_file(new_data['photo'], new_data['name'])
            st.success("Added as new item!"); time.sleep(1); st.rerun()
        else:
            c_row = int(choice)
            existing = matches[matches['Original_Row'] == c_row].iloc[0]
            new_total = int(existing['Current Qty']) + new_data['qty']
            
            row_data = sh.get(f"A{c_row}:N{c_row}") 
            row_vals = row_data[0] if row_data else [""] * 14
            row_vals += [""] * (14 - len(row_vals)) 
            
            if new_data.get('date'): row_vals[0] = new_data['date']
            if new_data.get('po'): row_vals[1] = new_data['po']
            if new_data.get('vendor'): row_vals[2] = new_data['vendor']
            if new_data.get('item_no'): row_vals[3] = new_data['item_no']
            if new_data.get('lqty'): row_vals[7] = new_data['lqty']
            if new_data.get('price'): row_vals[8] = new_data['price']
            if new_data.get('location'): row_vals[9] = new_data['location']
            row_vals[10] = new_total
            row_vals[12] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if new_data.get('photo_url'): row_vals[13] = new_data['photo_url']
            
            safe_gspread_update(sh, f"A{c_row}:N{c_row}", [row_vals])
            
            if new_data.get('photo'): save_uploaded_file(new_data['photo'], existing['Item Name'])
            
            st.success("Item combined successfully!"); time.sleep(1); st.rerun()

@st.dialog("➕ Quick Add Item")
def quick_add_dialog(df, category):
    qn = st.text_input("Item Name")
    qp = st.text_input("Part Number (PN)")
    c1, c2 = st.columns(2)
    qq = c1.number_input("Qty", 0)
    qs = c2.number_input("Safety Stock", 0)
    
    st.caption("Item Photo (Choose one)")
    qimg = st.file_uploader("Upload File", type=['png','jpg','jpeg'], label_visibility="collapsed")
    qimg_url = st.text_input("Or paste Image URL", placeholder="https://example.com/img.png")
    
    if qp.strip():
        matches = find_fuzzy_duplicates(df, qp.strip())
        if not matches.empty:
            st.warning(f"⚠️ Found {len(matches)} item(s) with similar Part Numbers.")
            options = {"NEW": "✨ Add as Brand New Item"}
            for _, r in matches.iterrows():
                options[r['Original_Row']] = f"📦 Combine: {r['Item Name']} (PN: {r['PN']}) - Qty: {r['Current Qty']}"
            
            choice = st.radio("Choose action:", options.keys(), format_func=lambda x: options[x])
            
            if st.button("Confirm Action", type="primary", width="stretch"):
                if not qn.strip() or not qp.strip(): 
                    st.error("Item Name and PN are required.")
                    return
                if choice == "NEW":
                    append_new_item_to_sheet(["", "", "", "", category, qn.strip(), qp.strip(), "", "", "", qq, qs, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qimg_url.strip()])
                    if qimg: save_uploaded_file(qimg, qn)
                    st.toast(f"Added {qn}!"); time.sleep(1); st.rerun()
                else:
                    c_row = int(choice)
                    existing = matches[matches['Original_Row'] == c_row].iloc[0]
                    new_total = int(existing['Current Qty']) + qq
                    
                    row_data = sh.get(f"A{c_row}:N{c_row}")
                    row_vals = row_data[0] if row_data else [""] * 14
                    row_vals += [""] * (14 - len(row_vals))
                    row_vals[10] = new_total
                    row_vals[12] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if qimg_url.strip(): row_vals[13] = qimg_url.strip()
                    
                    safe_gspread_update(sh, f"A{c_row}:N{c_row}", [row_vals])
                    if qimg: save_uploaded_file(qimg, existing['Item Name'])
                    
                    st.success("Item combined!"); time.sleep(1); st.rerun()
            return 

    if st.button("Add Item", type="primary", width="stretch"):
        if not qn.strip() or not qp.strip(): st.error("Item Name and PN are required.")
        else:
            append_new_item_to_sheet(["", "", "", "", category, qn.strip(), qp.strip(), "", "", "", qq, qs, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qimg_url.strip()])
            if qimg: save_uploaded_file(qimg, qn)
            st.toast(f"Added {qn}!"); time.sleep(1); st.rerun()


# --- 5. PAGE MODULES ---
def page_inventory():
    df = get_data() 
    col1, col2 = st.columns([4, 1])
    col1.title("📦 Maintenance Inventory")
    col1.caption("Created by Deanie Iskandar • Version 1.0.0")
    logo = next((os.path.join(IMAGE_DIR, f"WALogo.{e}") for e in ['png', 'jpg', 'jpeg'] if os.path.exists(os.path.join(IMAGE_DIR, f"WALogo.{e}"))), None)
    if logo: col2.image(logo, width="stretch")
    st.divider()

    if df.empty or 'Category' not in df.columns:
        return st.info("No items available. Please add items via the 'Add New Item' page.")

    # VIEW 3: ACTIVE ITEM EDITOR
    if st.session_state.active_item is not None:
        if st.button("⬅️ Back", width=90):
            st.session_state.active_item = None
            st.rerun()
            
        item_subset = df[df['Original_Row'] == st.session_state.active_item]
        if item_subset.empty: return st.warning("Item no longer exists.")
        
        item = item_subset.iloc[0]
        c_row = int(item['Original_Row'])
        
        active_pn = str(item['PN']).strip()
        duplicates = find_fuzzy_duplicates(df, active_pn, current_row_exclude=c_row)
        
        if not duplicates.empty:
            st.warning(f"⚠️ **Action Required:** Found {len(duplicates)} similar Part Number(s) in the system.")
            if st.button("🔄 Merge Duplicates", width="stretch"):
                merge_duplicates_dialog(item, duplicates, c_row)
            st.divider()
        
        if st.session_state.cat_change_msg:
            st.success(st.session_state.cat_change_msg); st.session_state.cat_change_msg = None 
            
        st.header(item['Item Name'])
        st.caption(f"**Part Number (PN):** `{item['PN']}` &nbsp;&nbsp;|&nbsp;&nbsp; **Category:** `{item['Category']}`")
        
        c_img, c_ctrl = st.columns([1, 1.2])
        with c_img:
            st.image(get_item_image(item['Item Name'], item.get('Image URL', '')), width="stretch")
            if st.button("📸 Update Photo", width="stretch"): update_photo_dialog(item, c_row)
        
        with c_ctrl:
            d1, d2 = st.columns(2)
            if d1.button("✏️ Edit Details", width="stretch"): edit_details_dialog(item, c_row)
            if d2.button("🗑️ Delete Item", width="stretch"): delete_item_dialog(item['Item Name'], c_row)
            st.write("") 
            
            qty, safety = int(item['Current Qty']), int(item['Safety Stock Qty'])
            st.metric("Current Stock", qty, delta=qty - safety)
            val = st.number_input("Amount", min_value=1, value=1)
            mode = st.radio("Action", ["Take (-)", "Add (+)"], horizontal=True)
            
            if st.button("Confirm Transaction", type="primary", width="stretch"):
                safe_update_cell(sh, c_row, 11, qty + val if mode == "Add (+)" else qty - val)
                safe_update_cell(sh, c_row, 13, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                st.toast("Transaction complete!"); st.session_state.active_item = None; st.rerun()

    # VIEWS 1 & 2: CATEGORY / SEARCH (HTML RESPONSIVE GRID)
    else:
        sq = st.text_input("🔍 Search by Part Number (PN)", placeholder="Type a PN...").strip()
        st.write("") 
        if sq:
            st.subheader(f"Search Results for '{sq}'")
            results = find_fuzzy_duplicates(df, sq)
            if results.empty: st.warning("No items found.")
            else: st.html(render_items_grid_html(df, search_results=results))
        else:
            if not st.session_state.selected_category:
                st.subheader("📂 Select Category")
                st.html(render_category_grid_html(df))
            else:
                col_b, col_a = st.columns([1, 1])
                with col_b:
                    if st.button("⬅️ Back", width=90):
                        st.session_state.selected_category = None
                        st.rerun()
                with col_a:
                    if st.button(f"➕ Add New Item to {st.session_state.selected_category}", type="primary", width="stretch"):
                        quick_add_dialog(df, st.session_state.selected_category)
                        
                st.subheader(f"📦 {st.session_state.selected_category}")
                st.html(render_items_grid_html(df, category=st.session_state.selected_category))


def page_low_stock():
    df = get_data()
    st.title("⚠️ Low Stock Alerts")
    st.caption("Items requiring immediate restocking"); st.divider()
    if df.empty or 'Current Qty' not in df.columns: return st.info("No data available.")
    
    ls = df[df['Current Qty'] < df['Safety Stock Qty']].copy()
    if ls.empty: st.success("All items are at or above safety stock levels! 🎉")
    else:
        st.error(f"Found {len(ls)} item(s) below safety stock.")
        ls['Deficit'] = ls['Safety Stock Qty'] - ls['Current Qty']
        display_cols = ['Category', 'Item Name', 'PN', 'Current Qty', 'Safety Stock Qty', 'Deficit']
        st.dataframe(ls[[c for c in display_cols if c in ls.columns]], width="stretch", hide_index=True, on_select="ignore")


def page_master_list():
    df = get_data()
    st.title("📋 Master List")
    if df.empty: st.info("No data available.")
    else: st.dataframe(df.drop(columns=['Original_Row', 'Image URL'], errors='ignore'), width="stretch", hide_index=True, height=600, on_select="ignore")


def page_add_new():
    df = get_data()
    st.title("➕ Add Multiple New Items")
    cats = ["-- NEW CATEGORY --"] + sorted(df['Category'].dropna().unique().tolist() if not df.empty and 'Category' in df.columns else [])
    
    with st.form("add_item_form"):
        c1, c2 = st.columns(2)
        cat_c, n_cat = c1.selectbox("Category *", cats), c1.text_input("New Category Name")
        n_name, n_pn = c1.text_input("Item Name *"), c1.text_input("Part Number (PN) *")
        c1.caption("--- Optional ---"); n_item, n_loc, n_date = c1.text_input("Item No"), c1.text_input("Location"), c1.date_input("Purchase Date", value=None)
        
        n_qty, n_safe = c2.number_input("Current Qty *", 0), c2.number_input("Safety Stock *", 0)
        
        st.caption("--- Item Photo (Choose One) ---")
        n_img = c2.file_uploader("Upload Image File", type=['png','jpg','jpeg'], label_visibility="collapsed")
        n_img_url = c2.text_input("Or paste Image URL", placeholder="https://example.com/img.png")
        
        c2.caption("--- Purchase Details ---"); n_ven, n_po = c2.text_input("Vendor"), c2.text_input("PO Number")
        n_lqty, n_price = c2.columns(2)[0].number_input("Last Qty Bought", 0), c2.columns(2)[1].number_input("Unit Price", 0.0)
        
        if st.form_submit_button("Add Item to System"):
            f_cat = n_cat.strip() if n_cat.strip() else cat_c
            if f_cat == "-- NEW CATEGORY --": st.error("Valid Category required.")
            elif not n_name.strip() or not n_pn.strip(): st.error("Item Name and Part Number required.")
            else:
                matches = find_fuzzy_duplicates(df, n_pn.strip())
                new_data = {
                    'name': n_name.strip(),
                    'pn': n_pn.strip(),
                    'item_no': n_item.strip(),
                    'location': n_loc.strip(),
                    'qty': n_qty,
                    'safe': n_safe,
                    'date': n_date.strftime("%Y-%m-%d") if n_date else "",
                    'po': n_po.strip(),
                    'vendor': n_ven.strip(),
                    'lqty': n_lqty if n_lqty > 0 else n_qty,
                    'price': n_price if n_price > 0 else "",
                    'photo': n_img,
                    'photo_url': n_img_url.strip()
                }
                
                if not matches.empty:
                    combine_item_dialog(new_data, matches, f_cat)
                else:
                    append_new_item_to_sheet([new_data['date'], new_data['po'], new_data['vendor'], new_data['item_no'], f_cat, new_data['name'], new_data['pn'], new_data['lqty'], new_data['price'], new_data['location'], new_data['qty'], new_data['safe'], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), new_data['photo_url']])
                    
                    if n_img: save_uploaded_file(n_img, new_data['name'])
                    st.success(f"Added {new_data['name']}!"); st.balloons()


# --- 6. MAIN EXECUTION (NAVIGATION) ---
inventory_page = st.Page(page_inventory, title="Inventory Update", icon="📦", default=True)
alerts_page = st.Page(page_low_stock, title="Low Stock Alerts", icon="⚠️")
master_page = st.Page(page_master_list, title="Master List", icon="📋")
add_page = st.Page(page_add_new, title="Add New Item", icon="➕")

pg = st.navigation([inventory_page, alerts_page, master_page, add_page])

if "last_page" not in st.session_state:
    st.session_state.last_page = pg.title

if st.session_state.last_page != pg.title:
    st.session_state.selected_category = None
    st.session_state.active_item = None
    st.session_state.last_page = pg.title

pg.run()
