from uuid import uuid4

from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from .forms import PersonnelForm
from .models import Personnel
from .personnel import clear_personnel_cache


@require_GET
def personnel_list(request):
    search = request.GET.get('search', '').strip()[:100]
    roster = Personnel.objects.filter(is_active=True)
    if search:
        roster = roster.filter(name__icontains=search)
    category = request.GET.get('category', '')
    if category:
        roster = roster.filter(category=category)
    page = Paginator(roster, 10).get_page(request.GET.get('page', 1))
    return JsonResponse({
        'html': render_to_string('partials/_personnel_management_rows.html', {'personnels': page}, request=request),
        'total_count': page.paginator.count, 'page': page.number,
        'total_pages': page.paginator.num_pages,
        'has_previous': page.has_previous(), 'has_next': page.has_next(),
    })


@require_POST
def personnel_save(request, personnel_id=None):
    form = PersonnelForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'error': ' '.join(message for errors in form.errors.values() for message in errors)}, status=400)
    name, category = form.cleaned_data['name'], form.cleaned_data['category']
    try:
        with transaction.atomic():
            # Serialize roster edits so aliases cannot move between identities.
            roster = list(Personnel.objects.select_for_update())
            target = get_object_or_404(Personnel, pk=personnel_id, is_active=True) if personnel_id else None
            for item in roster:
                if target and item.pk == target.pk:
                    continue
                if name.casefold() in {value.casefold() for value in [item.name, *item.previous_names]}:
                    return JsonResponse({'ok': False, 'error': 'That name already belongs to an existing or removed personnel record. Choose a different name to keep work histories separate.'}, status=409)
            if target:
                if target.name != name and target.name not in target.previous_names:
                    target.previous_names = [*target.previous_names, target.name]
                target.name, target.category = name, category
                target.save(update_fields=['name', 'category', 'previous_names'])
            else:
                target = Personnel.objects.create(slug=f'personnel-{uuid4().hex}', name=name, category=category)
    except IntegrityError:
        return JsonResponse({'ok': False, 'error': 'That personnel name already exists.'}, status=409)
    clear_personnel_cache()
    return JsonResponse({'ok': True, 'message': f'{target.name} was saved.'})


@require_POST
def personnel_delete(request, personnel_id):
    with transaction.atomic():
        target = get_object_or_404(Personnel.objects.select_for_update(), pk=personnel_id, is_active=True)
        target.is_active = False
        target.save(update_fields=['is_active'])
    clear_personnel_cache()
    return JsonResponse({'ok': True, 'message': f'{target.name} was removed from the active roster. Completed concerns, work history and ratings are preserved.'})
