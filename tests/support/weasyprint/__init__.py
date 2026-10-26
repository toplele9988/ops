"""Import-only test double: never generates or validates a PDF."""
class HTML:
    def __init__(self,*args,**kwargs):
        pass
    def write_pdf(self,*args,**kwargs):
        raise RuntimeError('pdf_renderer_not_available_in_synthetic_test')
