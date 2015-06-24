from googleads import dfp
from pylons import g

from reddit_dfp.lib import utils
from reddit_dfp.lib.dfp import DfpService
from reddit_dfp.lib.merge import merge_deep
from reddit_dfp.services import (
    orders_service,
)

NATIVE_SIZE = {
    "width": "1",
    "height": "1",
}
LINE_ITEM_DEFAULTS = {
    "creativeRotationType": "OPTIMIZED",
    "creativePlaceholders": [{
        "size": NATIVE_SIZE
    }],
    "reserveAtCreation": False,
    "primaryGoal": {
        "goalType": "DAILY",
        "unitType": "IMPRESSIONS",
        "units": 0,
    },
    "targeting": {
        "inventoryTargeting": {
            "targetedAdUnits": [{
                "adUnitId": "mw_card_test_1",
            }],
        },
    },
}


def _date_to_string(date, format="%d/%m/%y"):
    return date.strftime(format)


def _get_campaign_name(campaign):
    return ("%s [%s-%s]" % (
                    campaign.link_id,
                    _date_to_string(campaign.start_date),
                    _date_to_string(campaign.end_date)))[:255]

def _get_platform(campaign):
    if campaign.platform == "desktop":
        return "WEB"
    if campaign.platform == "mobile":
        return "MOBILE"
    else:
        return "ANY"


def _get_cost_type(campaign):
    return "CPM" # everything is CPM currently


def _priority_to_lineitem_type(priority):
    from r2.models import promo

    if priority == promo.HIGH:
        return "SPONSORSHIP"
    elif priority == promo.MEDIUM:
        return "STANDARD"
    elif priority == promo.REMNANT:
        return "BULK"
    elif priority == promo.HOUSE:
        return "HOUSE"


def _campaign_to_lineitem(campaign, order=None, existing=None):
    if not (existing or order):
        raise ValueError("must either pass an order or an existing lineitem.")

    lineitem = {
        "name": _get_campaign_name(campaign),
        "startDateTime": utils.datetime_to_dfp_datetime(campaign.start_date),
        "endDateTime": utils.datetime_to_dfp_datetime(campaign.end_date),
        "lineItemType": _priority_to_lineitem_type(campaign.priority),
        "costPerUnit": utils.dollars_to_dfp_money(campaign.cpm / 100),
        "costType": _get_cost_type(campaign),
        "targetPlatform": _get_platform(campaign),
        "skipInventoryCheck": campaign.priority.inventory_override,
        "primaryGoal": {
            "units": campaign.impressions,
        },
    }

    if existing:
        return merge_deep(existing, lineitem)
    else:
        return merge_deep(lineitem, LINE_ITEM_DEFAULTS, {
            "orderId": order["id"],
            "externalId": campaign._fullname,
        })


def get_lineitem(campaign):
    dfp_lineitem_service = DfpService("LineItemService")

    values = [{
        "key": "externalId",
        "value": {
            "xsi_type": "TextValue",
            "value": campaign._fullname,
        },
    }]
    query = "WHERE externalId = :externalId"
    statement = dfp.FilterStatement(query, values, 1)
    response = dfp_lineitem_service.execute(
                    "getLineItemsByStatement",
                    statement.ToStatement())

    if ("results" in response and len(response["results"])):
        return response["results"][0]
    else:
        return None

def create_lineitem(user, campaign):
    dfp_lineitem_service = DfpService("LineItemService")
    order = orders_service.upsert_order(user)

    lineitem = _campaign_to_lineitem(campaign, order=order)
    lineitems = dfp_lineitem_service.execute("createLineItems", [lineitem])

    return lineitems[0]

def upsert_lineitem(user, campaign):
    dfp_lineitem_service = DfpService("LineItemService")
    lineitem = get_lineitem(campaign)

    if not lineitem:
        return create_lineitem(user, campaign)

    if lineitem["isArchived"]:
        raise ValueError("cannot update archived lineitem (lid: %s, cid: %s)" %
                (lineitem["id"], campaign._id))

    updated = _campaign_to_lineitem(campaign, existing=lineitem)
    lineitems = dfp_lineitem_service.execute("updateLineItems", [updated])

    return lineitems[0]


def associate_with_creative(lineitem, creative):
    dfp_association_service = DfpService("LineItemCreativeAssociationService")

    lineitem_id = lineitem["id"]
    creative_id = creative["id"]

    values = [{
        "key": "lineItemId",
        "value": {
            "xsi_type": "NumberValue",
            "value": lineitem_id,
        },
    }, {
        "key": "creativeId",
        "value": {
            "xsi_type": "NumberValue",
            "value": creative_id,
        },
    }]
    query = "WHERE lineItemId = :lineItemId AND creativeId = :creativeId"
    statement = dfp.FilterStatement(query, values, 1)

    response = dfp_association_service.execute(
                    "getLineItemCreativeAssociationsByStatement",
                    statement.ToStatement())

    if ("results" in response and len(response["results"])):
        association = response["results"][0]
    else:
        associations = dfp_association_service.execute(
            "createLineItemCreativeAssociations",
            [{
                "lineItemId": lineitem_id,
                "creativeId": creative_id,
            }])

        return associations[0]


def deactivate(campaign):
    dfp_association_service = DfpService("LineItemCreativeAssociationService")
    lineitem = get_lineitem(campaign)

    if not lineitem:
        return True

    lineitem_id = lineitem["id"]
    values = [{
        "key": "lineItemId",
        "value": {
            "xsi_type": "NumberValue",
            "value": lineitem_id
        },
    }, {
        "key": "status",
        "value": {
            "xsi_type": "TextValue",
            "value": "ACTIVE"
        },
    }]

    query = "WHERE lineItemId = :lineItemId AND status = :status"
    statement = dfp.FilterStatement(query, values, 1)

    response = dfp_association_service.execute(
            "getLineItemCreativeAssociationsByStatement",
            statement.ToStatement())

    return result and int(result["numChanges"]) > 0
