import json
import urllib
from functools import update_wrapper

from django.conf import settings
from django.conf.urls import patterns, url
from django.contrib.admin import ModelAdmin, StackedInline, TabularInline
from django.contrib.admin.options import csrf_protect_m, InlineModelAdmin
from django.contrib.admin.util import flatten_fieldsets, unquote, get_deleted_objects
from django.contrib.contenttypes.generic import GenericStackedInline, GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.urlresolvers import reverse
from django.db import router, transaction
from django.forms.models import model_to_dict
from django.forms.util import ErrorList
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect
from django.template import TemplateDoesNotExist
from django.template.defaultfilters import capfirst
from django.template.loader import find_template
from django.utils.encoding import iri_to_uri, force_unicode
from django.utils.functional import curry
from django.utils.translation import get_language, ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from .fields import SortableForeignKey
from .forms import TranslatableModelForm, translatable_inlineformset_factory, translatable_modelform_factory
from .manager import FALLBACK_LANGUAGES
from .utils import get_cached_translation, get_language_name, get_is_sortable, get_translation


class TranslatableModelAdminMixin(object):
    query_language_key = 'language'

    def all_translations(self, obj):
        """
        use this to display all languages the object has been translated to
        in the changelist view:

        class MyAdmin(admin.ModelAdmin):
            list_display = ('__str__', 'all_translations',)

        """
        if obj and obj.pk:
            languages = []
            current_language = get_language()
            for language in obj.get_available_languages():
                if language == current_language:
                    languages.append(u'<strong>%s</strong>' % language)
                else:
                    languages.append(language)
            return u' '.join(languages)
        else:
            return ''

    all_translations.allow_tags = True
    all_translations.short_description = _('all translations')

    def get_available_languages(self, obj):
        return obj.get_available_languages() if obj else []

    def get_language_tabs(self, request, available_languages):
        tabs = []
        get = dict(request.GET)
        language = self._language(request)
        for key, name in settings.LANGUAGES:
            get.update({'language': key})
            url = '%s://%s%s?%s' % (request.is_secure() and 'https' or 'http', request.get_host(), request.path,
                                    urllib.urlencode(get))
            if language == key:
                status = 'current'
            elif key in available_languages:
                status = 'available'
            else:
                status = 'empty'
            tabs.append((url, name, key, status))
        return tabs

    def _language(self, request):
        return request.GET.get(self.query_language_key, get_language())


class TransortableAdmin(ModelAdmin, TranslatableModelAdminMixin):
    form = TranslatableModelForm
    ordering = ('order', 'id')

    change_form_template = 'admin/transortable/change_form.html'
    deletion_not_allowed_template = 'admin/transortable/deletion_not_allowed.html'

    sortable_change_list_with_sort_link_template = 'admin/transortable/change_list_with_sort_link.html'
    sortable_change_form_template = 'admin/transortable/change_form.html'
    sortable_change_list_template = 'admin/transortable/change_list.html'
    sortable_javascript_includes_template = 'admin/transortable/shared/javascript_includes.html'

    change_form_template_extends = 'admin/change_form.html'
    change_list_template_extends = 'admin/change_list.html'

    def _get_sortable_foreign_key(self):
        sortable_foreign_key = None
        for field in self.model._meta.fields:
            if isinstance(field, SortableForeignKey):
                sortable_foreign_key = field
                break
        return sortable_foreign_key

    def get_urls(self):

        def wrap(view):
            def wrapper(*args, **kwargs):
                return self.admin_site.admin_view(view)(*args, **kwargs)

            return update_wrapper(wrapper, view)

        return patterns(
            '',
            # delete translation view
            url(r'^(.+)/delete-translation/(.+)/$',
                wrap(self.delete_translation_view),
                name='%s_%s_delete_translation' % (self.model._meta.app_label, self.model._meta.module_name)),
            # change order view
            url(r'^sorting/do-sorting/(?P<model_type_id>\d+)/$',
                wrap(self.do_sorting_view),
                name='admin_do_sorting'),
            # this view shows a link to the drag-and-drop view
            url(r'^sort/$',
                wrap(self.sort_view),
                name='admin_sort'),
        ) + super(TransortableAdmin, self).get_urls()

    def get_form(self, request, obj=None, **kwargs):
        """
        Returns a Form class for use in the admin add view. This is used by
        add_view and change_view.
        """

        if self.declared_fieldsets:
            fields = flatten_fieldsets(self.declared_fieldsets)
        else:
            fields = None
        if self.exclude is None:
            exclude = []
        else:
            exclude = list(self.exclude)
        exclude.extend(kwargs.get("exclude", []))
        exclude.extend(self.get_readonly_fields(request, obj))
        # Exclude language_code, adding it again to the instance is done by
        # the LanguageAwareCleanMixin (see translatable_modelform_factory)
        exclude.append('language_code')
        old_formfield_callback = curry(self.formfield_for_dbfield, request=request)
        defaults = {
            "form": self.form,
            "fields": fields,
            "exclude": exclude,
            "formfield_callback": old_formfield_callback,
        }
        defaults.update(kwargs)
        language = self._language(request)
        return translatable_modelform_factory(language, self.model, **defaults)

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        lang_code = self._language(request)
        lang = get_language_name(lang_code)
        available_languages = self.get_available_languages(obj)
        context['title'] = '%s (%s)' % (context['title'], lang)
        context['current_is_translated'] = lang_code in available_languages
        context['allow_deletion'] = len(available_languages) > 1
        context['language_tabs'] = self.get_language_tabs(request, available_languages)
        context['base_template'] = self.get_change_form_base_template()
        return super(TransortableAdmin, self).render_change_form(request, context, add, change, form_url, obj)

    def response_change(self, request, obj):
        redirect = super(TransortableAdmin, self).response_change(request, obj)
        uri = iri_to_uri(request.path)
        app_label, model_name = self.model._meta.app_label, self.model._meta.module_name
        if redirect['Location'] in (uri, "../add/", reverse('admin:%s_%s_add' % (app_label, model_name))):
            if self.query_language_key in request.GET:
                redirect['Location'] = '%s?%s=%s' % (redirect['Location'], self.query_language_key,
                                                     request.GET[self.query_language_key])
        return redirect

    def deletion_not_allowed(self, request, obj, language_code):
        opts = self.model._meta
        app_label = opts.app_label
        object_name = force_unicode(opts.verbose_name)

        return render(
            request,
            self.deletion_not_allowed_template,
            {'object': obj.master,
             'language_code': language_code,
             'opts': opts,
             'app_label': app_label,
             'language_name': get_language_name(language_code),
             'object_name': object_name}
        )

    def delete_model_translation(self, request, obj):
        obj.delete()

    def get_object(self, request, object_id):
        obj = super(TransortableAdmin, self).get_object(request, object_id)
        if not obj:
            queryset = self.model.objects.untranslated()
            model = self.model
            try:
                object_id = model._meta.pk.to_python(object_id)
                obj = queryset.get(pk=object_id)
            except (model.DoesNotExist, ValidationError):
                return None
            new_translation = model._meta.translations_model()
            new_translation.language_code = self._language(request)
            new_translation.master = obj
            setattr(obj, model._meta.translations_cache, new_translation)
        return obj

    def queryset(self, request):
        language = self._language(request)
        languages = [language, ]
        for lang in FALLBACK_LANGUAGES:
            if not lang in languages:
                languages.append(lang)
        qs = self.model._default_manager.untranslated().use_fallbacks(*languages)
        # TODO: this should be handled by some parameter to the ChangeList.
        ordering = getattr(self, 'ordering', None) or ()  # otherwise we might try to *None, which is bad ;)
        if ordering:
            qs = qs.order_by(*ordering)
        return qs

    def get_change_form_base_template(self):
        opts = self.model._meta
        app_label = opts.app_label
        search_templates = [
            "admin/%s/%s/change_form.html" % (app_label, opts.object_name.lower()),
            "admin/%s/change_form.html" % app_label,
            "admin/change_form.html"
        ]
        for template in search_templates:
            try:
                find_template(template)
                return template
            except TemplateDoesNotExist:
                pass
        else:  # pragma: no cover
            pass

    def changelist_view(self, request, extra_context=None):
        """
        If the model that inherits Sortable has more than one object,
        its sort order can be changed. This view adds a link to the
        object_tools block to take people to the view to change the sorting.
        """

        if get_is_sortable(self.queryset(request)):
            self.change_list_template = self.sortable_change_list_with_sort_link_template
            self.is_sortable = True

        if extra_context is None:
            extra_context = {}

        extra_context.update({
            'change_list_template_extends': self.change_list_template_extends
        })

        return super(TransortableAdmin, self).changelist_view(request, extra_context=extra_context)

    @csrf_protect_m
    @transaction.commit_on_success
    def delete_translation_view(self, request, object_id, language_code):
        """
        The 'delete translation' admin view for this model.
        """

        opts = self.model._meta
        app_label = opts.app_label
        translations_model = opts.translations_model

        try:
            obj = translations_model.objects.select_related('maser').get(master__pk=unquote(object_id),
                                                                         language_code=language_code)
        except translations_model.DoesNotExist:
            raise Http404

        if not self.has_delete_permission(request, obj):
            raise PermissionDenied

        if len(self.get_available_languages(obj.master)) <= 1:
            return self.deletion_not_allowed(request, obj, language_code)

        using = router.db_for_write(translations_model)

        # Populate deleted_objects, a data structure of all related objects that will also be deleted.

        deleted_objects, perms_needed, protected = get_deleted_objects(
            [obj], translations_model._meta, request.user, self.admin_site, using
        )

        lang = get_language_name(language_code)

        if request.POST:  # The user has already confirmed the deletion.
            if perms_needed:
                raise PermissionDenied
            obj_display = '%s translation of %s' % (lang, force_unicode(obj.master))
            self.log_deletion(request, obj, obj_display)
            self.delete_model_translation(request, obj)

            self.message_user(
                request,
                _('The %(name)s "%(obj)s" was deleted successfully.') % {
                    'name': force_unicode(opts.verbose_name),
                    'obj': force_unicode(obj_display)
                }
            )

            if not self.has_change_permission(request, None):
                return redirect('admin:index')
            return redirect('admin:%s_%s_changelist' % (opts.app_label, opts.module_name))

        object_name = '%s Translation' % force_unicode(opts.verbose_name)

        if perms_needed or protected:
            title = _("Cannot delete %(name)s") % {"name": object_name}
        else:
            title = _("Are you sure?")

        context = {
            "title": title,
            "object_name": object_name,
            "object": obj,
            "deleted_objects": deleted_objects,
            "perms_lacking": perms_needed,
            "protected": protected,
            "opts": opts,
            "app_label": app_label,
        }

        # in django > 1.4 root_path is removed
        if hasattr(self.admin_site, 'root_path'):
            context.update({"root_path": self.admin_site.root_path})

        return render(
            request,
            self.delete_confirmation_template or [
                "admin/%s/%s/delete_confirmation.html" % (app_label, opts.object_name.lower()),
                "admin/%s/delete_confirmation.html" % app_label,
                "admin/delete_confirmation.html"
            ],
            context
        )

    def sort_view(self, request):
        """
        Custom admin view that displays the objects as a list whose sort
        order can be changed via drag-and-drop.
        """
        opts = self.model._meta
        has_perm = request.user.has_perm('{0}.{1}'.format(opts.app_label, opts.get_change_permission()))

        objects = self.queryset(request)

        # Determine if we need to regroup objects relative to a
        # foreign key specified on the model class that is extending Sortable.
        # Legacy support for 'sortable_by' defined as a model property
        sortable_by_property = getattr(self.model, 'sortable_by', None)

        # `sortable_by` defined as a SortableForeignKey
        sortable_by_fk = self._get_sortable_foreign_key()
        sortable_by_class_is_sortable = get_is_sortable(objects)

        if sortable_by_property:
            # backwards compatibility for < 1.1.1, where sortable_by was a
            # classmethod instead of a property
            try:
                sortable_by_class, sortable_by_expression = sortable_by_property()
            except (TypeError, ValueError):
                sortable_by_class = self.model.sortable_by
                sortable_by_expression = sortable_by_class.__name__.lower()

            sortable_by_class_display_name = sortable_by_class._meta.verbose_name_plural

        elif sortable_by_fk:
            # get sortable by properties from the SortableForeignKey
            # field - supported in 1.3+
            sortable_by_class_display_name = sortable_by_fk.rel.to._meta.verbose_name_plural
            sortable_by_class = sortable_by_fk.rel.to
            sortable_by_expression = sortable_by_fk.name.lower()

        else:
            # model is not sortable by another model
            sortable_by_class = sortable_by_expression = \
                sortable_by_class_display_name = sortable_by_class_is_sortable = None

        if sortable_by_property or sortable_by_fk:
            # Order the objects by the property they are sortable by,
            # then by the order, otherwise the regroup
            # template tag will not show the objects correctly
            objects = objects.order_by(sortable_by_expression, 'order')

        try:
            verbose_name_plural = opts.verbose_name_plural.__unicode__()
        except AttributeError:
            verbose_name_plural = opts.verbose_name_plural

        context = {
            'title': u'Drag and drop {0} to change display order'.format(capfirst(verbose_name_plural)),
            'opts': opts,
            'app_label': opts.app_label,
            'has_perm': has_perm,
            'objects': objects,
            'group_expression': sortable_by_expression,
            'sortable_by_class': sortable_by_class,
            'sortable_by_class_is_sortable': sortable_by_class_is_sortable,
            'sortable_by_class_display_name': sortable_by_class_display_name,
            'sortable_javascript_includes_template': self.sortable_javascript_includes_template
        }

        return render(
            request,
            self.sortable_change_list_template,
            context
        )

    def add_view(self, request, form_url='', extra_context=None):
        if extra_context is None:
            extra_context = {}

        extra_context.update({
            'change_form_template_extends': self.change_form_template_extends
        })

        return super(TransortableAdmin, self).add_view(request, form_url, extra_context=extra_context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        self.has_sortable_tabular_inlines = False
        self.has_sortable_stacked_inlines = False

        if extra_context is None:
            extra_context = {}

        extra_context.update({
            'change_form_template_extends': self.change_form_template_extends
        })

        for klass in self.inlines:
            if issubclass(klass, (TransortableTabularInline, TransortableGenericTabularInline)):
                self.has_sortable_tabular_inlines = True
            if issubclass(klass, (TransortableStackedInline, TransortableGenericStackedInline)):
                self.has_sortable_stacked_inlines = True

        if self.has_sortable_tabular_inlines or self.has_sortable_stacked_inlines:
            self.change_form_template = self.sortable_change_form_template

            extra_context.update({
                'sortable_javascript_includes_template': self.sortable_javascript_includes_template,
                'has_sortable_tabular_inlines': self.has_sortable_tabular_inlines,
                'has_sortable_stacked_inlines': self.has_sortable_stacked_inlines
            })

        return super(TransortableAdmin, self).change_view(request, object_id, form_url, extra_context=extra_context)

    @csrf_exempt
    def do_sorting_view(self, request, model_type_id=None):
        """
        This view sets the ordering of the objects for the model type
        and primary keys passed in. It must be an Ajax POST.
        """

        if request.is_ajax() and request.method == 'POST':
            try:
                indexes = list(map(str, request.POST.get('indexes', []).split(',')))
                klass = ContentType.objects.get(id=model_type_id).model_class()
                objects_dict = dict([(str(obj.pk), obj) for obj in klass.objects.filter(pk__in=indexes)])

                if '-order' in klass._meta.ordering:  # desc order
                    start_object = max(objects_dict.values(), key=lambda x: getattr(x, 'order'))
                    start_index = getattr(start_object, 'order') \
                        or len(indexes)
                    step = -1
                else:  # 'order' is default, asc order
                    start_object = min(objects_dict.values(), key=lambda x: getattr(x, 'order'))
                    start_index = getattr(start_object, 'order') or 0
                    step = 1

                for index in indexes:
                    obj = objects_dict.get(index)
                    setattr(obj, 'order', start_index)
                    obj.save()
                    start_index += step

                response = {'objects_sorted': True}
            except (KeyError, IndexError, klass.DoesNotExist, AttributeError):
                pass
        else:
            response = {'objects_sorted': False}
        return HttpResponse(json.dumps(response, ensure_ascii=False), mimetype='application/json')


class InlineModelForm(TranslatableModelForm):
    def __init__(self, data=None, files=None, auto_id='id_%s', prefix=None, initial=None, error_class=ErrorList,
                 label_suffix=':', empty_permitted=False, instance=None):

        opts = self._meta
        model_opts = opts.model._meta
        object_data = {}
        language = getattr(self, 'language', get_language())
        if instance is not None:
            trans = get_cached_translation(instance)
            if not trans or trans.language_code != language:
                try:
                    trans = get_translation(instance, language)
                except model_opts.translations_model.DoesNotExist:
                    trans = None
            if trans:
                object_data = model_to_dict(trans, opts.fields, opts.exclude)
                # Dirty hack that swaps the id from the translation id, to the master id
                # This is necessary, because we in this case get the untranslated instance,
                # and thereafter get the correct translation on save.
                if 'id' in object_data:
                    object_data['id'] = trans.master.id
        if initial is not None:
            object_data.update(initial)
        initial = object_data
        super(TranslatableModelForm, self).__init__(data, files, auto_id, prefix, initial, error_class, label_suffix,
                                                    empty_permitted, instance)


class TransortableInlineModelAdmin(InlineModelAdmin, TranslatableModelAdminMixin):
    form = InlineModelForm

    change_form_template = 'admin/transortable/change_form.html'
    deletion_not_allowed_template = 'admin/transortable/deletion_not_allowed.html'

    def get_formset(self, request, obj=None, **kwargs):
        """
        Returns a BaseInlineFormSet class for use in admin add/change views.
        """

        if self.declared_fieldsets:
            fields = flatten_fieldsets(self.declared_fieldsets)
        else:
            fields = None
        if self.exclude is None:
            exclude = []
        else:
            exclude = list(self.exclude)
        exclude.extend(kwargs.get("exclude", []))
        exclude.extend(self.get_readonly_fields(request, obj))
        # if exclude is an empty list we use None, since that's the actual default
        exclude = exclude or None
        defaults = {
            "form": self.get_form(request, obj),
            #"formset": self.formset,
            "fk_name": self.fk_name,
            "fields": fields,
            "exclude": exclude,
            "formfield_callback": curry(self.formfield_for_dbfield, request=request),
            "extra": self.extra,
            "max_num": self.max_num,
            "can_delete": self.can_delete,
        }
        defaults.update(kwargs)
        language = self._language(request)
        return translatable_inlineformset_factory(language, self.parent_model, self.model, **defaults)

    def get_urls(self):
        def wrap(view):
            def wrapper(*args, **kwargs):
                return self.admin_site.admin_view(view)(*args, **kwargs)

            return update_wrapper(wrapper, view)

        info = self.model._meta.app_label, self.model._meta.module_name

        return patterns(
            '',
            url(r'^(.+)/delete-translation/(.+)/$',
                wrap(self.delete_translation),
                name='%s_%s_delete_translation' % info),
        )

    def get_form(self, request, obj=None, **kwargs):
        """
        Returns a Form class for use in the admin add view. This is used by
        add_view and change_view.
        """
        if self.declared_fieldsets:
            fields = flatten_fieldsets(self.declared_fieldsets)
        else:
            fields = None
        if self.exclude is None:
            exclude = []
        else:
            exclude = list(self.exclude)
        exclude.extend(kwargs.get("exclude", []))
        exclude.extend(self.get_readonly_fields(request, obj))
        # Exclude language_code, adding it again to the instance is done by
        # the LanguageAwareCleanMixin (see translatable_modelform_factory)
        exclude.append('language_code')
        old_formfield_callback = curry(self.formfield_for_dbfield,
                                       request=request)
        defaults = {
            "form": self.form,
            "fields": fields,
            "exclude": exclude,
            "formfield_callback": old_formfield_callback,
        }
        defaults.update(kwargs)
        language = self._language(request)
        return translatable_modelform_factory(language, self.model, **defaults)

    def response_change(self, request, obj):
        redirect = super(TransortableAdmin, self).response_change(request, obj)
        uri = iri_to_uri(request.path)
        if redirect['Location'] in (uri, "../add/"):
            if self.query_language_key in request.GET:
                redirect['Location'] = '%s?%s=%s' % (redirect['Location'], self.query_language_key,
                                                     request.GET[self.query_language_key])
        return redirect

    def queryset(self, request):
        qs = self.model._default_manager.order_by('order')
        self.model.is_sortable = get_is_sortable(qs)
        return qs


class TransortableTabularInline(TransortableInlineModelAdmin, TabularInline):
    """Custom template that enables sorting for tabular inlines"""
    template = 'admin/transortable/edit_inline/tabular.html'


class TransortableStackedInline(TransortableInlineModelAdmin, StackedInline):
    """Custom template that enables sorting for stacked inlines"""
    template = 'admin/transortable/edit_inline/stacked.html'


class TransortableGenericTabularInline(TransortableInlineModelAdmin, GenericTabularInline):
    """Custom template that enables sorting for tabular inlines"""
    template = 'admin/transortable/edit_inline/tabular.html'


class TransortableGenericStackedInline(TransortableInlineModelAdmin, GenericStackedInline):
    """Custom template that enables sorting for stacked inlines"""
    template = 'admin/transortable/edit_inline/stacked.html'