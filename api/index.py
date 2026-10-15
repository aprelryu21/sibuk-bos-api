from flask import Flask, request, jsonify
import pdfplumber
import base64
import os
import re
import uuid
import tempfile

app = Flask(__name__)

# Fungsi untuk membersihkan titik pemisah ribuan agar menjadi angka murni
def clean_number(text):
    if not text: return 0
    text = str(text).strip()
    # Pisahkan angka desimal sen di belakang koma jika ada
    if ',' in text:
        text = text.split(',')[0]
    text = text.replace('.', '').strip()
    try:
        return int(text)
    except:
        return 0

@app.route('/api/extract', methods=['POST'])
def extract_pdf():
    try:
        data = request.get_json()
        if not data or 'pdf_base64' not in data:
            return jsonify({"error": "Tidak ada data pdf_base64"}), 400

        # Menerima file PDF dari Google Apps Script dan menyimpannya sementara
        pdf_data = base64.b64decode(data['pdf_base64'])
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"bku_{uuid.uuid4().hex}.pdf")
        
        with open(temp_path, "wb") as f:
            f.write(pdf_data)

        grouped_data = {}
        last_bukti_per_keg_rek = {}
        last_bukti_per_keg = {}
        pending_taxes = []

        def deteksi_jenis_pajak(uraian_lower):
            if "setor ppn" in uraian_lower:
                return "Nominal_PPN"
            elif "pph 22" in uraian_lower or "pph pasal 22" in uraian_lower:
                return "Nominal_PPh22"
            elif "pph 23" in uraian_lower or "pph pasal 23" in uraian_lower:
                return "Nominal_PPh23"
            elif "pajak daerah" in uraian_lower or "sspd" in uraian_lower or "pajak restoran" in uraian_lower or "pb1" in uraian_lower:
                return "Nominal_SSPD"
            elif "setor pph" in uraian_lower:
                return "Nominal_PPh21"
            return None

        with pdfplumber.open(temp_path) as pdf:
            for page in pdf.pages:
                # Buat garis potong horizontal otomatis tepat di atas setiap tanggal agar baris tanpa garis tidak menyatu
                words = page.extract_words()
                date_lines = [
                    w["top"] - 1.5
                    for w in words
                    if w["x0"] < 120 and re.match(r"^\d{2}-\d{2}-\d{4}$", w["text"].strip())
                ]
                
                if date_lines:
                    table = page.extract_table({
                        "vertical_strategy": "lines",
                        "horizontal_strategy": "lines",
                        "explicit_horizontal_lines": date_lines
                    })
                else:
                    table = page.extract_table()

                if not table:
                    continue
                
                for row in table:
                    if len(row) < 7:
                        continue
                    
                    tanggal = re.sub(r"\s+", "", str(row[0] or ""))
                    kode_keg = re.sub(r"\s+", "", str(row[1] or ""))
                    kode_rek = re.sub(r"\s+", " ", str(row[2] or "")).strip()
                    no_bukti = re.sub(r"\s+", "", str(row[3] or ""))
                    uraian = re.sub(r"\s+", " ", str(row[4] or "")).strip()
                    pengeluaran_str = str(row[6] or "0").strip()

                    if no_bukti == "4" and uraian == "5":
                        continue

                    if not uraian or uraian.lower() in ["uraian", "jumlah"]:
                        continue

                    uraian_lower = uraian.lower()

                    # 1. ATURAN ABAIKAN: Saldo, Bunga Bank, dan Terima Pajak
                    if "saldo bank" in uraian_lower or "saldo tunai" in uraian_lower:
                        continue
                    if uraian_lower in ["bunga bank", "pajak bunga"]:
                        continue
                    if uraian_lower.startswith("terima pph") or uraian_lower.startswith("terima ppn") or uraian_lower.startswith("terima pajak"):
                        continue

                    # 2. ATURAN PAJAK (Baris tanpa No. Bukti / Setor Pajak)
                    if not no_bukti:
                        jenis_pajak = deteksi_jenis_pajak(uraian_lower)
                        if jenis_pajak:
                            nilai_pajak = clean_number(pengeluaran_str)
                            if nilai_pajak > 0:
                                pending_taxes.append({
                                    "tanggal": tanggal,
                                    "kode_keg": kode_keg,
                                    "kode_rek": kode_rek,
                                    "jenis": jenis_pajak,
                                    "nominal": nilai_pajak
                                })
                        continue

                    # 3. ATURAN TRANSAKSI UTAMA: Ekstrak dan gabungkan data ganda
                    if no_bukti:
                        nominal = clean_number(pengeluaran_str)
                        detail_item = {"uraian": uraian, "nominal": nominal}
                        
                        if kode_keg:
                            last_bukti_per_keg[kode_keg] = no_bukti
                        if kode_keg and kode_rek:
                            last_bukti_per_keg_rek[(kode_keg, kode_rek)] = no_bukti

                        if no_bukti in grouped_data:
                            grouped_data[no_bukti]["Nominal_Pengeluaran"] += nominal
                            
                            if uraian not in grouped_data[no_bukti]["Uraian_BKU"]:
                                grouped_data[no_bukti]["Uraian_BKU"] += " | " + uraian
                            
                            grouped_data[no_bukti]["Detail_Belanja"].append(detail_item)
                        else:
                            grouped_data[no_bukti] = {
                                "Tanggal_Penerimaan": tanggal,
                                "Kode_Kegiatan": kode_keg,
                                "Kode_Rekening": kode_rek,
                                "No_Bukti": no_bukti,
                                "Uraian_BKU": uraian,
                                "Nominal_Pengeluaran": nominal,
                                "Nominal_PPh21": 0,
                                "Nominal_PPN": 0,
                                "Nominal_PPh23": 0,
                                "Nominal_SSPD": 0,
                                "Nominal_PPh22": 0,
                                "Detail_Belanja": [detail_item]
                            }

                        # Pasangkan pajak yang tercetak tepat di atas/sebelum No. Bukti ini
                        sisa_pending = []
                        for pt in pending_taxes:
                            cocok_keg_rek = (pt["kode_keg"] == kode_keg and pt["kode_rek"] == kode_rek)
                            cocok_keg_saja = (pt["kode_keg"] == kode_keg and (not pt["kode_rek"] or not kode_rek))
                            if cocok_keg_rek or cocok_keg_saja:
                                grouped_data[no_bukti][pt["jenis"]] += pt["nominal"]
                            else:
                                sisa_pending.append(pt)
                        pending_taxes = sisa_pending

        # Jika masih ada pajak yang tercetak setelah transaksi BNU (misal setor pajak di akhir bulan),
        # pasangkan ke transaksi terakhir yang memiliki Kode Kegiatan & Kode Rekening yang sesuai
        for pt in pending_taxes:
            target_bkt = last_bukti_per_keg_rek.get((pt["kode_keg"], pt["kode_rek"])) or last_bukti_per_keg.get(pt["kode_keg"])
            if target_bkt and target_bkt in grouped_data:
                grouped_data[target_bkt][pt["jenis"]] += pt["nominal"]

        # Ubah dictionary grouped_data kembali menjadi list agar sesuai format JSON awal
        extracted_data = list(grouped_data.values())
        return jsonify({"status": "success", "data": extracted_data}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

if __name__ == '__main__':
    app.run(debug=True)
# redeploy
# force redeploy v2
