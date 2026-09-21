#!/usr/bin/env python3
# generate_test_pdf.py - Genera facturas PDF realistas para testing OCR
# Uso: python3 generate_test_pdf.py

from fpdf import FPDF
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "test-files")

class InvoicePDF(FPDF):
    def header(self):
        # Logo placeholder
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(0, 51, 102)
        self.set_text_color(255, 255, 255)
        self.cell(0, 12, "  FACTURA ELECTRÓNICA CFDI 4.0", fill=True, new_x="LMARGIN", new_y="NEXT", align="L")
        self.set_text_color(0, 0, 0)
        self.ln(5)

    def footer(self):
        self.set_y(-20)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")
        self.cell(0, 10, "Generado para testing - Expense Reconciler MVP", align="R")

def create_invoice_gas():
    """Factura de gas - proveedor típico"""
    pdf = InvoicePDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    # Datos del emisor (proveedor)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "GAS INDUSTRIAL DEL NORTE SA DE CV", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "RFC: GIN850315AB1", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Regimen: General de Ley Personas Morales", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Av. Industrial 4567, Col. Parque Industrial", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Monterrey, NL, CP 66600, Mexico", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Línea separadora
    pdf.set_draw_color(0, 51, 102)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    # Datos del receptor (cliente)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 6, "DATOS DEL RECEPTOR:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "Nombre: TAQUERIA EL BUEN SABOR SA DE CV", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "RFC: TBS920115XYZ", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Uso CFDI: G03 - Gastos en general", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Regimen: General de Ley Personas Morales", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Domicilio Fiscal: Av. Revolucion 123, Col. Centro", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Monterrey, NL, CP 64000", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Datos de la factura
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 6, "DATOS DEL COMPROBANTE:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "Folio Fiscal: A1B2C3D4-E5F6-7890-ABCD-EF1234567890", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Serie: A | Folio: 001234", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Fecha Expedicion: 2025-01-15T10:30:00", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Fecha Certificacion: 2025-01-15T10:30:05", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Forma Pago: 03 - Transferencia electronica", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Metodo Pago: PUE - Pago en una sola exhibicion", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Moneda: MXN | Tipo Cambio: 1.0000", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Tabla de conceptos
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    col_widths = [8, 65, 20, 22, 22, 22, 22]
    headers = ["#", "Descripcion", "Cant.", "Unidad", "P.Unit", "Importe", "IVA"]
    
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    
    concepts = [
        ("1", "GAS LP ESTACIONARIO - 500 KG", "500", "KG", "18.50", "9,250.00", "1,480.00"),
        ("2", "TRANSPORTE Y ENTREGA", "1", "SERV", "850.00", "850.00", "136.00"),
        ("3", "MANTENIMIENTO TANQUE ESTACIONARIO", "1", "SERV", "2,500.00", "2,500.00", "400.00"),
    ]
    
    for c in concepts:
        for i, val in enumerate(c):
            align = "C" if i in [0, 2, 3] else "R" if i >= 4 else "L"
            pdf.cell(col_widths[i], 6, val, border=1, align=align)
        pdf.ln()
    
    pdf.ln(5)
    
    # Totales
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(137, 7, "", border=0)
    pdf.cell(22, 7, "Subtotal:", border=1, align="R")
    pdf.cell(22, 7, "12,600.00", border=1, align="R")
    pdf.ln()
    pdf.cell(137, 7, "", border=0)
    pdf.cell(22, 7, "IVA (16%):", border=1, align="R")
    pdf.cell(22, 7, "2,016.00", border=1, align="R")
    pdf.ln()
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(137, 8, "", border=0)
    pdf.cell(22, 8, "TOTAL:", border=1, fill=True, align="R")
    pdf.cell(22, 8, "14,616.00", border=1, fill=True, align="R")
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    
    # Sello digital
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, "Sello Digital del CFDI:\nBASE64_ENCODED_SEAL_DATA_AQUÍ...")
    pdf.ln(3)
    pdf.multi_cell(0, 4, "Sello Digital del SAT:\nBASE64_ENCODED_SAT_SEAL...")
    pdf.ln(3)
    pdf.cell(0, 4, "Cadena Original: ||1.0|A1B2C3D4...|2025-01-15T10:30:00|...||", new_x="LMARGIN", new_y="NEXT")
    
    # QR Code placeholder
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(0, 4, "[CODIGO QR - Verificacion en portal SAT]", align="C", new_x="LMARGIN", new_y="NEXT")
    
    output_path = os.path.join(OUTPUT_DIR, "factura_gas_industrial.pdf")
    pdf.output(output_path)
    print(f"✓ Generado: {output_path}")
    return output_path


def create_invoice_refrescos():
    """Factura de refrescos - proveedor típico"""
    pdf = InvoicePDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    # Emisor
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "REFRESCOS Y BEBIDAS DE MEXICO SA DE CV", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "RFC: RBM900520CD2", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Blvd. Diaz Ordaz 3000, Col. Valle Oriente", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "San Pedro Garza Garcia, NL, CP 66269", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    pdf.set_draw_color(200, 0, 0)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    # Receptor
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 6, "RECEPTOR:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "TAQUERIA EL BUEN SABOR SA DE CV", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "RFC: TBS920115XYZ", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Uso CFDI: G03", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Comprobante
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 6, "COMPROBANTE:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "Serie: F | Folio: 005678", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Fecha: 2025-01-16T14:22:00", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Forma Pago: 03 | Metodo: PUE", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Moneda: MXN", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Conceptos
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(200, 0, 0)
    pdf.set_text_color(255, 255, 255)
    col_w = [8, 70, 18, 20, 22, 22, 22]
    hdrs = ["#", "Producto", "Cant.", "Unidad", "P.Unit", "Importe", "IVA"]
    for i, h in enumerate(hdrs):
        pdf.cell(col_w[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    
    items = [
        ("1", "COCA-COLA 600ML RETORNABLE", "48", "PZA", "18.00", "864.00", "138.24"),
        ("2", "SPRITE 600ML RETORNABLE", "36", "PZA", "17.50", "630.00", "100.80"),
        ("3", "FANTA NARANJA 600ML RETORNABLE", "24", "PZA", "17.50", "420.00", "67.20"),
        ("4", "AGUA CIEL 1.5L", "60", "PZA", "14.00", "840.00", "134.40"),
        ("5", "COCA-COLA ZERO 600ML", "24", "PZA", "18.50", "444.00", "71.04"),
        ("6", "ENVASE RETORNABLE (DEPOSITO)", "192", "PZA", "4.00", "768.00", "122.88"),
    ]
    
    for item in items:
        for i, val in enumerate(item):
            align = "C" if i in [0, 2, 3] else "R" if i >= 4 else "L"
            pdf.cell(col_w[i], 6, val, border=1, align=align)
        pdf.ln()
    
    pdf.ln(5)
    
    # Totales
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(137, 7, "", border=0)
    pdf.cell(22, 7, "Subtotal:", border=1, align="R")
    pdf.cell(22, 7, "3,966.00", border=1, align="R")
    pdf.ln()
    pdf.cell(137, 7, "", border=0)
    pdf.cell(22, 7, "IVA (16%):", border=1, align="R")
    pdf.cell(22, 7, "634.56", border=1, align="R")
    pdf.ln()
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(200, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(137, 8, "", border=0)
    pdf.cell(22, 8, "TOTAL:", border=1, fill=True, align="R")
    pdf.cell(22, 8, "4,600.56", border=1, fill=True, align="R")
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(0, 4, "Sello CFDI: ... | Sello SAT: ... | Cadena Original: ...", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 4, "[QR CODE - Verificacion SAT]", align="C", new_x="LMARGIN", new_y="NEXT")
    
    output_path = os.path.join(OUTPUT_DIR, "factura_refrescos_mexico.pdf")
    pdf.output(output_path)
    print(f"✓ Generado: {output_path}")
    return output_path


def create_invoice_papeleria():
    """Factura de papelería - ticket simple"""
    pdf = InvoicePDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "PAPELERIA Y SUMINISTROS CENTRAL SA DE CV", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "RFC: PSC880214EF3", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Calzada del Valle 800, Col. del Valle", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "San Pedro Garza Garcia, NL, CP 66220", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    pdf.set_draw_color(0, 100, 0)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 6, "RECEPTOR:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "TAQUERIA EL BUEN SABOR SA DE CV", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "RFC: TBS920115XYZ", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Uso CFDI: G03", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(90, 6, "COMPROBANTE:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(90, 5, "Serie: T | Folio: 009876", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Fecha: 2025-01-17T09:15:00", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(90, 5, "Forma Pago: 03 | Metodo: PUE", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Conceptos - ticket estilo
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(0, 100, 0)
    pdf.set_text_color(255, 255, 255)
    col_w = [8, 80, 18, 20, 22, 22, 22]
    hdrs = ["#", "Articulo", "Cant.", "Unidad", "P.Unit", "Importe", "IVA"]
    for i, h in enumerate(hdrs):
        pdf.cell(col_w[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 8)
    
    items = [
        ("1", "PAPEL BOND OFICIO 75G CAJA 5 RMS", "3", "CAJA", "480.00", "1,440.00", "230.40"),
        ("2", "BOLIGRAFO AZUL CAJA 12 PZAS", "5", "CAJA", "180.00", "900.00", "144.00"),
        ("3", "CARPETA ARCHIVO OFICIO COLOR", "20", "PZA", "18.50", "370.00", "59.20"),
        ("4", "POST-IT NOTAS 76X76MM PAQ 12", "6", "PAQ", "95.00", "570.00", "91.20"),
        ("5", "CINTA ADHESIVA TRANSPARENTE 12MM", "10", "PZA", "25.00", "250.00", "40.00"),
    ]
    
    for item in items:
        for i, val in enumerate(item):
            align = "C" if i in [0, 2, 3] else "R" if i >= 4 else "L"
            pdf.cell(col_w[i], 6, val, border=1, align=align)
        pdf.ln()
    
    pdf.ln(5)
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(137, 7, "", border=0)
    pdf.cell(22, 7, "Subtotal:", border=1, align="R")
    pdf.cell(22, 7, "3,530.00", border=1, align="R")
    pdf.ln()
    pdf.cell(137, 7, "", border=0)
    pdf.cell(22, 7, "IVA (16%):", border=1, align="R")
    pdf.cell(22, 7, "564.80", border=1, align="R")
    pdf.ln()
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(0, 100, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(137, 8, "", border=0)
    pdf.cell(22, 8, "TOTAL:", border=1, fill=True, align="R")
    pdf.cell(22, 8, "4,094.80", border=1, fill=True, align="R")
    pdf.ln()
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(0, 4, "Sello CFDI: ... | Sello SAT: ... | [QR CODE]", new_x="LMARGIN", new_y="NEXT")
    
    output_path = os.path.join(OUTPUT_DIR, "factura_papeleria_central.pdf")
    pdf.output(output_path)
    print(f"✓ Generado: {output_path}")
    return output_path


def create_simple_receipt():
    """Ticket simple estilo ticket de caja (no CFDI)"""
    pdf = InvoicePDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "TICKET DE COMPRA", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "MERCADO LOCAL DON PEPE", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "RFC: MLP950101ABC", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Av. Principal 123, Col. Centro", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Tel: 81-1234-5678", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    pdf.set_draw_color(0, 0, 0)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Fecha: 15/01/2025  14:32:10", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Ticket: 0001-0002345", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Cajero: MARIA G.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    
    items = [
        ("TORTA DE PASTOR", 1, "85.00"),
        ("REFRESCO COCA 600ML", 2, "35.00"),
        ("AGUA NATURAL 1L", 1, "18.00"),
        ("PAPAS FRITAS", 1, "25.00"),
    ]
    
    for desc, qty, price in items:
        total = float(price) * qty
        pdf.cell(100, 6, f"{desc}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(100, 5, f"    {qty} x ${price} = ${total:.2f}", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(100, 6, "SUBTOTAL:", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(80, 6, "$198.00", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(100, 6, "IVA (16%):", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(80, 6, "$31.68", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(100, 8, "TOTAL:", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(80, 8, "$229.68", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "Pago: EFECTIVO $250.00  Cambio: $20.32", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "¡Gracias por su compra!", align="C", new_x="LMARGIN", new_y="NEXT")
    
    output_path = os.path.join(OUTPUT_DIR, "ticket_mercado_simple.pdf")
    pdf.output(output_path)
    print(f"✓ Generado: {output_path}")
    return output_path


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Generando PDFs de prueba en: {OUTPUT_DIR}")
    print("-" * 50)
    
    create_invoice_gas()
    create_invoice_refrescos()
    create_invoice_papeleria()
    create_simple_receipt()
    
    print("-" * 50)
    print("¡Listo! 4 PDFs generados para testing OCR:")
    print("  1. factura_gas_industrial.pdf      - CFDI completo (gas)")
    print("  2. factura_refrescos_mexico.pdf    - CFDI completo (bebidas)")
    print("  3. factura_papeleria_central.pdf   - CFDI completo (oficina)")
    print("  4. ticket_mercado_simple.pdf       - Ticket simple (no CFDI)")