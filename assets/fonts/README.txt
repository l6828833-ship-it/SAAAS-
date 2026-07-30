Drop optional TTF/TTC files here to replace the built-in typefaces.

Naming decides the slot (case-insensitive):
  *bold*    -> bold weight
  *italic*  -> italic weight
  *display* -> month names and cover title
  anything else -> regular weight

Both the PDF engine (artisan_forge/pdf/fonts.py) and the mockup compositor
(artisan_forge/mockups/draw_utils.py) pick these up automatically. With no files
here, the PDF uses Helvetica/Times and the mockups use a system UI font.

Only add fonts you are licensed to embed in commercial products.
