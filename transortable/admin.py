from django import VERSION
from django.contrib.admin import TabularInline, StackedInline

from adminsortable.admin import SortableAdmin, SortableInlineBase, NonSortableParentAdmin
from adminsortable.utils import get_is_sortable
from hvad.admin import TranslatableAdmin, TranslatableInlineModelAdmin


class TransortableAdmin(TranslatableAdmin, SortableAdmin):
    change_form_template = 'admin/transortable/change_form.html'
    sortable_change_form_template = change_form_template


class NonSortableTranslatableAdmin(TransortableAdmin, NonSortableParentAdmin):
    pass


class TransortableBaseInline(TranslatableInlineModelAdmin, SortableInlineBase):
    change_form_template = 'admin/transortable/change_form.html'
    sortable_change_form_template = change_form_template

    def get_queryset(self, request):
        queryset = super(TransortableBaseInline, self).get_queryset(request)
        self.model.is_sortable = get_is_sortable(queryset)
        return queryset

    if VERSION < (1, 6):
        queryset = get_queryset


class TransortableTabularInline(TransortableBaseInline, TabularInline):
    if VERSION <= (1, 5):
        template = 'admin/transortable/edit_inline/tabular-1.5.x.html'
    else:
        template = 'admin/transortable/edit_inline/tabular.html'


class TransortableStackedInline(TransortableBaseInline, StackedInline):
    if VERSION <= (1, 5):
        template = 'admin/transortable/edit_inline/stacked-1.5.x.html'
    else:
        template = 'admin/transortable/edit_inline/stacked.html'
