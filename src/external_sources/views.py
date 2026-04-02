from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from external_sources.models import ExternalSource
from external_sources.models import ExternalSourceScan
from external_sources.serializers import ExternalSourceScanSerializer
from external_sources.serializers import ExternalSourceSerializer
from external_sources.tasks import scan_external_source


class ExternalSourceViewSet(viewsets.ModelViewSet):
    queryset = ExternalSource.objects.all()
    serializer_class = ExternalSourceSerializer
    permission_classes = (IsAuthenticated,)

    @action(detail=True, methods=["post"])
    def scan(self, request, pk=None):
        """Trigger a scan of this external source."""
        source = self.get_object()
        mode = request.data.get("mode", "delta")
        scan_external_source.delay(source.pk, mode=mode)
        return Response(
            {"detail": f"Scan dispatched for source '{source.code}' in {mode} mode."},
            status=202,
        )

    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        """List recent scan runs for this source."""
        source = self.get_object()
        runs = ExternalSourceScan.objects.filter(source=source)[:20]
        serializer = ExternalSourceScanSerializer(runs, many=True)
        return Response(serializer.data)
