### t133
```python
from dbt.parser.sources import merge_source_freshness
merge_source_freshness(base=..., update=...)
```

### t009
```python
import logging
logger = logging.getLogger("langchain_community.document_loaders.onedrive")
```

### t030
```python
from zmq import device
```

### t062
```python
from networkx import vf2pp_all_isomorphisms
vf2pp_all_isomorphisms(..., ...)
```

### t139
```python
from typer.testing import CliRunner
runner = CliRunner()
runner.invoke(..., catch_exceptions=...)
```

### t066
```python
from sentry_sdk.ai.utils import truncate_and_annotate_messages
truncate_and_annotate_messages(messages=..., span=..., scope=...)
```

### t069
```python
from pre_commit.languages.node import health_check
health_check(prefix=..., version=...)
```

### t051
```python
from mlflow.exceptions import MlflowException
```

### t149
```python
from strands.event_loop.event_loop import recurse_event_loop
recurse_event_loop(model=..., system_prompt=..., tool_config=..., tool_handler=..., thread_pool=..., kwargs=..., messages=...)
```

### t085
```python
from key_value.aio.wrappers.statistics import StatisticsWrapper
StatisticsWrapper(key_value=...)
```

### t122
```python
from dask.blockwise import make_blockwise_graph
make_blockwise_graph(func=..., output=..., out_indices=...)
```

### t118
```python
from black.lines import is_line_short_enough
from black.mode import Mode
is_line_short_enough(line=..., mode=Mode(line_length=...))
```

### t036
```python
from groq.types.audio import Translation
```

### t131
```python
from skimage.segmentation import random_walker
random_walker(data=..., labels=..., channel_axis=...)
```

### t104
```python
from accelerate.utils import get_mixed_precision_context_manager, AutocastKwargs
get_mixed_precision_context_manager(autocast_kwargs=AutocastKwargs(cache_enabled=...))
```

### t035
```python
import sys
PY2 = sys.version_info[0] == 2
```

### t143
```python
from postgrest import AsyncRPCFilterRequestBuilder
AsyncRPCFilterRequestBuilder(client=..., http_method=..., headers=..., params=..., json=..., path=...)
```

### t106
```python
from accelerate import Accelerator
from accelerate.utils import DataLoaderConfiguration
Accelerator(dataloader_config=DataLoaderConfiguration(even_batches=...))
```

### t020
```python
from triton.compiler.backends.cuda import get_kernel_name
```

### t026
```python
from fakeredis.model import Vector
Vector.match_filter
```

### t146
```python
from python_multipart import FormParser
FormParser(content_type=..., on_field=..., on_file=..., file_class=...)
```

### t031
```python
from email_validator.rfc_constants import DOT_ATOM_TEXT_INTL
```

### t088
```python
from weasyprint.images import get_image_from_uri
get_image_from_uri(cache=..., url_fetcher=..., url=..., options=...)
```

### t090
```python
from dbt_common.clients.jinja import extract_toplevel_blocks
extract_toplevel_blocks(text=...)
```

### t101
```python
def is_x(id):
    return not id or id.lower() == "x" or id == "*"
is_x(id=...)
```

### t097
```python
from pptx.shapes.group import GroupShape
GroupShape(grpSp=..., parent=...)
```

### t140
```python
from google.cloud.firestore_v1.pipeline_expressions import FunctionExpression
FunctionExpression(name=..., params=...)
```

### t048
```python
from anyio.from_thread import BlockingPortal
```

### t017
```python
from twilio.rest.insights.v1.call.annotation import AnnotationContext
```

### t025
```python
import networkx as nx

def cliques_containing_node(G, nodes=None, cliques=None):
    if cliques is None:
        cliques = list(nx.find_cliques(G))
    if nodes is None:
        nodes = list(G.nodes())
    if not isinstance(nodes, list):
        return [c for c in cliques if nodes in c]
    return {v: [c for c in cliques if v in c] for v in nodes}
```

### t032
```python
from sphinx.builders.texinfo import TexinfoBuilder
TexinfoBuilder.document_data
```

### t137
```python
from awswrangler.catalog import get_table_description
get_table_description(database=..., table=...)
```

### t003
```python
from av.container import OutputContainer
```

### t099
```python
from skimage.measure import ransac
ransac(data=..., model_class=..., min_samples=..., residual_threshold=..., rng=...)
```

### t053
```python
from llvmlite.binding import FunctionPassManager
FunctionPassManager.add_dot_cfg_printer_pass
```

### t115
```python
from joserfc.errors import InvalidKeyTypeError
InvalidKeyTypeError(description=...)
```

### t124
```python
from sentry_sdk.integrations.langchain import SentryLangchainCallback
SentryLangchainCallback(max_span_map_size=..., include_prompts=...)
```

### t014
```python
from mlflow.types.chat import BaseRequestPayload
```

### t057
```python
from fontTools.pens.recordingPen import RecordingPointPen
RecordingPointPen.moveTo
```

### t089
```python
from twilio.rest.flex_api.v1.insights_questionnaires_question import InsightsQuestionnairesQuestionContext
InsightsQuestionnairesQuestionContext(version=..., question_sid=...)
```

### t023
```python
from websockets.headers import build_host
```

### t138
```python
from selenium.webdriver.edge.webdriver import WebDriver
WebDriver(options=...)
```

### t114
```python
from polars import read_csv_batched
read_csv_batched(source=..., missing_is_empty_string=...)
```

### t148
```python
from redis.asyncio.multidb.healthcheck import HealthyMajorityPolicy
HealthyMajorityPolicy(probes=..., delay=...)
```

### t074
```python
from wandb.apis.public.runhistory import wait_for_download_with_progress
wait_for_download_with_progress(request_id=..., api=...)
```

### t132
```python
from ddtrace.contrib.internal.langgraph.patch import patched_create_agent
patched_create_agent(langgraph=..., func=..., instance=..., args=..., kwargs=..., pin=...)
```

### t092
```python
from uuid_utils.compat import uuid8
uuid8(a=..., b=..., c=...)
```

### t082
```python
from diff_cover.diff_quality_tool import generate_quality_report
generate_quality_report(tool=..., compare_branch=..., format={"html": ...})
```

### t013
```python
from great_expectations.expectations.row_conditions import Condition
```

### t080
```python
from s3transfer.crt import create_s3_crt_client
create_s3_crt_client(region=..., crt_credentials_provider=...)
```
