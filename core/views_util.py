"""Small helpers shared by the CRUD views."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, default: str) -> str:
    """Return the ``next`` parameter only if it points back to this site."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    allowed = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return target if target and allowed else default


def form_page(
    request,
    form_class,
    *,
    instance=None,
    title,
    back,
    success_message,
    success_redirect=None,
    initial=None,
    intro="",
    template="core/form.html",
    extra=None,
    before_save=None,
):
    """Render and process a user-scoped ModelForm. ``form_class`` must accept ``user=``."""
    form = form_class(request.POST or None, instance=instance, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        if before_save:
            before_save(obj)
        obj.save()
        form.save_m2m()
        messages.success(request, success_message)
        return redirect(safe_next(request, success_redirect or back))
    context = {"form": form, "title": title, "back": back, "intro": intro, "next": request.GET.get("next", "")}
    context.update(extra or {})
    return render(request, template, context)


def delete_page(request, obj, *, title, warning, back, success_message, delete=None):
    """Confirm and perform a destructive action via POST only."""
    if request.method == "POST":
        (delete or (lambda o: o.delete()))(obj)
        messages.success(request, success_message)
        return redirect(safe_next(request, back))
    return render(
        request, "core/confirm_delete.html", {"object": obj, "title": title, "warning": warning, "back": back}
    )
