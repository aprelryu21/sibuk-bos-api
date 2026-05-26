from flask import Flask, request, jsonify
import pdfplumber
import base64
import os

app = Flask(__name__)

# Fungsi bantuan untuk membersihkan format angka
def clean_number(text):
    if not text: return 0
    # Membersihkan titik (ribuan) dan koma (desimal jika ada)
    text = str(text).replace('.', '').replace(',', '').strip()
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

        # Simpan file sementara
        pdf_data = base64.b64decode(data['pdf_base64'])
        temp_path = "/tmp/temp_bku.pdf" 
        with open(temp_path, "wb") as f:
            f.write(pdf_data)

        grouped_data = {}    # Menyimpan hasil akhir (No Bukti sebagai key)
        pending_taxes = {}   # Menyimpan potongan pajak sementara
        last_no_bukti = None # Variabel "Penyelamat" untuk baris tanpa nomor bukti

        with pdfplumber.open(temp_path) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table: continue
                
                for row in table:
                    # Pastikan baris memiliki data minimal (sesuai format tabel BKU)
                    if len(row) < 7: continue 
                    
                    tanggal = str(row[0] or "").strip().replace('\n', ' ')
                    kode_keg = str(row[1] or "").strip().replace('\n', ' ')
                    kode_rek = str(row[2] or "").strip().replace('\n', ' ')
                    no_bukti = str(row[3] or "").strip().replace('\n', ' ')
                    uraian = str(row[4] or "").strip().replace('\n', ' ')
                    pengeluaran_str = str(row[6] or "0").strip()

                    # Abaikan header atau footer
                    if no_bukti == "4" and uraian == "5": continue
                    if not uraian or uraian.lower() == "uraian": continue
                    if "saldo bank" in uraian.lower() or "saldo tunai" in uraian.lower(): continue
                    if "terima pph" in uraian.lower() or "terima ppn" in uraian.lower(): continue

                    # LOGIKA PAJAK
                    uraian_lower = uraian.lower()
                    if "setor ppn" in uraian_lower or "pph 23" in uraian_lower or "pajak daerah" in uraian_lower or "setor pph" in uraian_lower:
                        if kode_keg not in pending_taxes:
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}
                        
                        nominal_pajak = clean_number(pengeluaran_str)
                        if "setor ppn" in uraian_lower: pending_taxes[kode_keg]['ppn'] += nominal_pajak
                        elif "pph 23" in uraian_lower: pending_taxes[kode_keg]['pph23'] += nominal_pajak
                        elif "pajak daerah" in uraian_lower or "sspd" in uraian_lower: pending_taxes[kode_keg]['sspd'] += nominal_pajak
                        elif "setor pph" in uraian_lower: pending_taxes[kode_keg]['pph21'] += nominal_pajak
                        continue

                    # LOGIKA TRANSAKSI UTAMA
                    # Jika kolom No Bukti kosong, gunakan nomor bukti baris sebelumnya
                    current_no_bukti = no_bukti if no_bukti else last_no_bukti
                    
                    if current_no_bukti:
                        last_no_bukti = current_no_bukti # Update untuk baris berikutnya
                        
                        # Ambil pajak yang tertahan di kode kegiatan tersebut
                        pph21 = 0; ppn = 0; pph23 = 0; sspd = 0
                        if kode_keg in pending_taxes:
                            pph21 = pending_taxes[kode_keg]['pph21']
                            ppn = pending_taxes[kode_keg]['ppn']
                            pph23 = pending_taxes[kode_keg]['pph23']
                            sspd = pending_taxes[kode_keg]['sspd']
                            pending_taxes[kode_keg] = {'pph21': 0, 'ppn': 0, 'pph23': 0, 'sspd': 0}

                        nominal = clean_number(pengeluaran_str)
                        detail_item = {"uraian": uraian, "nominal": nominal}

                        if current_no_bukti in grouped_data:
                            # Jika transaksi sudah ada (rincian tambahan)
                            grouped_data[current_no_bukti]["Nominal_Pengeluaran"] += nominal
                            grouped_data[current_no_bukti]["Nominal_PPh21"] += pph21
                            grouped_data[current_no_bukti]["Nominal_PPN"] += ppn
                            grouped_data[current_no_bukti]["Nominal_PPh23"] += pph23
                            grouped_data[current_no_bukti]["Nominal_SSPD"] += sspd
                            if uraian not in grouped_data[current_no_bukti]["Uraian_BKU"]:
                                grouped_data[current_no_bukti]["Uraian_BKU"] += " | " + uraian
                            grouped_data[current_no_bukti]["Detail_Belanja"].append(detail_item)
                        else:
                            # Jika transaksi baru
                            grouped_data[current_no_bukti] = {
                                "Tanggal_Penerimaan": tanggal,
                                "Kode_Kegiatan": kode_keg,
                                "Kode_Rekening": kode_rek,
                                "No_Bukti": current_no_bukti,
                                "Uraian_BKU": uraian,
                                "Nominal_Pengeluaran": nominal,
                                "Nominal_PPh21": pph21,
                                "Nominal_PPN": ppn,
                                "Nominal_PPh23": pph23,
                                "Nominal_SSPD": sspd,
                                "Detail_Belanja": [detail_item]
                            }

        # Konversi hasil ke list
        extracted_data = list(grouped_data.values())

        if os.path.exists(temp_path):
            os.remove(temp_path)

        return jsonify({"status": "success", "data": extracted_data}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
