from adminsortable.models import Sortable as SortableModel
from hvad.models import TranslatableModel


class TransortableModel(TranslatableModel, SortableModel):

    class Meta(SortableModel.Meta):
        abstract = True
