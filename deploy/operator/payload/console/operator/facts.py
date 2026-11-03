"""Outbound grammar: a model may select evidence, never invent a factual sentence.

Every non-template sentence must equal a reviewed fact in the current archive
snapshot. Whole-sentence matching prevents changing units, values or promises
while citing an unrelated source containing the same number.
"""
import re

TEXT = {
 'es': {
  'ask_supplier': 'Hola, confirme las especificaciones, el embalaje, el precio actual, la validez de la oferta y el plazo de entrega.',
  'ask_forwarder': 'Hola, confirme el flete, la cobertura del destino, los impuestos, el tiempo de tránsito y la validez de la oferta.',
  'reply_customer': 'Gracias por su mensaje. Estoy comprobando los detalles y le responderé.',
  'quote_customer': 'Gracias. Por favor, revise la siguiente cotización.',
  'draft_pi': 'Revise este borrador de factura proforma. El pago no está confirmado.',
  'draft_order': 'Revise este borrador de pedido y confirme los detalles.',
  'shipping': 'Revise la planificación del envío y confirme los detalles.',
 },
 'fr': {
  'ask_supplier': 'Bonjour, merci de confirmer les spécifications, le conditionnement, le prix actuel, la validité du devis et le délai de livraison.',
  'ask_forwarder': 'Bonjour, merci de confirmer le fret, la couverture à destination, les taxes, le délai de transport et la validité du devis.',
  'reply_customer': 'Merci pour votre message. Je vérifie les détails et je reviens vers vous.',
  'quote_customer': 'Merci. Veuillez vérifier le devis suivant.',
  'draft_pi': 'Veuillez vérifier ce projet de facture proforma. Le paiement reste à confirmer.',
  'draft_order': 'Veuillez vérifier ce projet de commande et confirmer les détails.',
  'shipping': 'Veuillez vérifier les dispositions de livraison et confirmer les détails.',
 },
 'pt': {
  'ask_supplier': 'Olá, confirme as especificações, a embalagem, o preço atual, a validade da cotação e o prazo de entrega.',
  'ask_forwarder': 'Olá, confirme o frete, a cobertura no destino, os impostos, o prazo de transporte e a validade da cotação.',
  'reply_customer': 'Obrigado pela mensagem. Estou verificando os detalhes e retornarei.',
  'quote_customer': 'Obrigado. Confira a seguinte cotação.',
  'draft_pi': 'Confira este rascunho de fatura proforma. O pagamento ainda não foi confirmado.',
  'draft_order': 'Confira este rascunho de pedido e confirme os detalhes.',
  'shipping': 'Confira os preparativos do envio e confirme os detalhes.',
 },
 'en': {
  'ask_supplier': 'Hello, please confirm product specifications, packing, current price, quote validity and lead time for this inquiry.',
  'ask_forwarder': 'Hello, please confirm freight, destination coverage, taxes, transit time and quote validity for this inquiry.',
  'reply_customer': 'Thank you for your message. I am checking the details and will follow up.',
  'quote_customer': 'Thank you. Please review the following quotation.',
  'draft_pi': 'Please review this proforma invoice draft. Payment has not been confirmed.',
  'draft_order': 'Please review this order draft and confirm the details.',
  'shipping': 'Please review the shipping arrangements and confirm the details.',
 },
 'zh': {
  'ask_supplier': '您好，请核对本次询盘的产品规格、装箱资料、现价、报价有效期与交期。',
  'ask_forwarder': '您好，请核对本次询盘的运费、目的地覆盖、税费、时效与报价有效期。',
  'reply_customer': '谢谢您的消息，我正在核对资料，确认后回复您。',
  'quote_customer': '谢谢，请核对以下报价。',
  'draft_pi': '请核对这份形式发票草稿，收款尚未确认。',
  'draft_order': '请核对这份订单草稿并确认细节。',
  'shipping': '请核对发货安排并确认细节。',
 },
}


def locale(profile):
    language = profile.get('language', '')
    code = (language or '').split('-')[0]
    return code if code in TEXT else None


def usable(fact, contact, today):
    if fact.get('status') != 'confirmed' or not fact.get('source_ref') or not fact.get('observed_at'):
        return False
    from datetime import date
    try:
        observed = str(fact['observed_at'])[:10]
        if date.fromisoformat(observed) > date.fromisoformat(today):
            return False
        if fact.get('valid_until'):
            date.fromisoformat(fact['valid_until'])
    except (TypeError, ValueError):
        return False
    if fact.get('language') and fact['language'] != locale(contact):
        return False
    expiry = fact.get('valid_until')
    if expiry is not None and expiry < today:
        return False
    if fact.get('kind') in ('cost', 'freight', 'sale_price', 'lead_time', 'document') and not expiry:
        return False
    if contact.get('type', '').startswith('customer_'):
        return (fact.get('public') is True and fact.get('is_cost_price') is False
                and fact.get('confidential') is False
                and fact.get('customer_key') in (None, contact['key']))
    return fact.get('customer_key') in (None, contact['key']) or fact.get('kind') == 'requirement'


def opening(kind, profile):
    lang = locale(profile)
    text = TEXT[lang][kind]
    if profile.get('tone') == 'friendly':
        text = {'en':'Hi! ', 'zh':'您好呀，', 'es':'¡Hola! ', 'fr':'Bonjour ! ', 'pt':'Olá! '}[lang] + text
    return text


def compose(kind, profile, facts):
    lang = locale(profile)
    if lang is None:
        return [], 'language_required'
    return [opening(kind, profile), *[f['text'] for f in facts]], None


def guard(action, context, today):
    if action['kind'] not in TEXT['en']:
        return []
    profile = context['contacts'].get(action['contact_key'], {})
    lang = locale(profile)
    if lang is None:
        return ['language_required']
    known = {f['id']: f for f in context.get('facts', [])}
    selected = []
    for fid in action.get('fact_ids', []):
        f = known.get(fid)
        if not f or not usable(f, profile, today):
            return ['source_unusable']
        selected.append(f)
    expected = [opening(action['kind'], profile), *[f['text'] for f in selected]]
    if action.get('parts') != expected:
        return ['unsourced_content']
    if len(expected) > 12 or any(not isinstance(t, str) or not t.strip() or len(t) > 1800 for t in expected):
        return ['content_invalid']
    # Facts themselves are approved archive content; model prose never enters this channel.
    if action['kind'] == 'quote_customer' and not any(f.get('kind') == 'sale_price' for f in selected):
        return ['quote_missing']
    if action['kind'] in ('draft_pi', 'draft_order', 'shipping') and not action.get('document_ready'):
        return ['document_review_required']
    if action['kind'] in ('draft_pi', 'draft_order') and not any(f.get('kind') == 'document' for f in selected):
        return ['document_review_required']
    return []


def fingerprint(text):
    from .store import sha
    return sha(re.sub(r'\s+', ' ', text).strip().casefold())
