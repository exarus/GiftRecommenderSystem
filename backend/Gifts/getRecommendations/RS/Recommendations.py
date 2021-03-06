from bson.objectid import ObjectId
import math
from Gifts.getRecommendations.Requests import findingApi
from Gifts.getRecommendations.TextClasterisation import nlp
from Gifts.getRecommendations.DB import DB
from random import shuffle

page_size = 100  # todo in constant file


def remove_similar(items, n):
    """
    left only n items with same colour

    :param items: sorted by prediction
    :param n: number of similar to left
    :return: new list of items
    """
    cur_color = items[0]['prediction']
    count = 0
    new_items = []
    for item in items:
        if item['prediction'] != cur_color:
            cur_color = item['prediction']
            count = 0
        if count < n:
            count += 1
            new_items.append(item)
    return new_items



def convert_list_to_dict(items, key):
    """
    convert list of dict to dict
    :param items: list of dict
    :param key: which field of dict make key
    :return: dict, where keys are items[:][key]
    """
    new_dict = {}
    for item in items:
        cur_key = item[key]
        item.__delitem__(key)
        new_dict.update({cur_key: item})
    return new_dict


def get_list_from_category(category_id, item_filter):
    """
    find items from specified category in ebay by applying filter
    :param category_id:
    :param item_filter:
    :return: up to 30 items  // should be clarify
    """
    items = []

    # print type(category_id)
    # print item_filter
    response = findingApi.get_list_of_items("findItemsAdvanced",
                                            {'categoryId': category_id,
                                             'paginationInput': {'entriesPerPage': 100},
                                             'itemFilter': item_filter})
#todo empty response
    if 'item' not in response['searchResult']:  # if empty response
        return []
    for item in response['searchResult']['item']:
        try:
            items.append({'title': item['title'],
                          'galleryURL': item['galleryURL'], 'itemURL': item['viewItemURL'],
                          'price': item['sellingStatus']['convertedCurrentPrice'],
                          'categoryID': category_id, 'itemID': item['itemId']})
            # print item
        except Exception as e:
            print e.message

    shuffle(items)
    if len(items) > 10:  # left only by 3 items for each of ten category
        predictions = nlp.get_prediction([item['title'] for item in items])
        for i in range(len(items)):
            items[i]['prediction'] = int(predictions[i])
        items.sort(key=lambda x: x['prediction'])

        items = remove_similar(items, 3)

    return convert_list_to_dict(items, 'itemID')


def choose_categories(user, max_categories=10):
    """
    choose max_categories from user.categories
    60% top, 40% unseen
    :param user:
    :param max_categories:
    :return:
    """
    assert max_categories > 5
    items = []
    count = 0
    keys = user['categories'].keys()
    shuffle(keys)
    # add top categories
    for category_id in keys:
        if user['categories'][category_id]['votes'] > 3 and user['categories'][category_id]['rating'] >= 3:
            user['categories'][category_id]['used'] = True
            count += 1
            items.append(category_id)
        if count >= max_categories * 0.6:
            break
    # add unseen categories
    for category_id in keys:
        if not 'used' in user['categories'][category_id].keys() or user['categories'][category_id]['used'] is False:
            if user['categories'][category_id]['votes'] > 3 and user['categories'][category_id]['rating'] <= 0:
                continue
            user['categories'][category_id]['used'] = True
            count += 1
            items.append(category_id)
        if count >= max_categories:
            break
    return items


def generate_list_for_user(user, item_filter):
    """
    generate user.items - suggested items

    :param user:
    :param item_filter:
    :return:
    """
    item_filter.append({'name': 'Condition', 'value': 'New'})
    item_filter.append({'name': 'ListingType', 'value': 'FixedPrice'})
    categories_id = choose_categories(user)
    items = {}
    for category_id in categories_id:
        items.update(get_list_from_category(category_id, item_filter))
    return items


def generate_list(user_id, min_price=None, max_price=None):
    """
    :use generate_list_for_user
    generate list of suggested items for user
    :param user_id:
    :param min_price:
    :param max_price:
    :return:
    """
    user_id = ObjectId(user_id)
    item_filter = []
    if min_price is not None:
        item_filter.append({'name': 'MinPrice', 'value': min_price})
    if max_price is not None:
        item_filter.append({'name': 'MaxPrice', 'value': max_price})

    client = DB.get_client()

    try:
        user = client.GRS.users.find_one({"_id": user_id})
        assert user is not None
        items = generate_list_for_user(user, item_filter)
        client.GRS.users.find_one_and_update({"_id": user_id}, {'$set': {'categories': user['categories'],
                                                                         'cur_page': 0,
                                                                         'items': items}})
    finally:
        client.close()


def rate(user_id, item_id, rating):
    """
    change rating of category of item
    :param user_id:
    :param item_id:
    :param rating:
    :return:
    """
    client = DB.get_client()
    try:
        user = client.GRS.users.find_one({"_id": user_id})
        assert user is not None
        category_id = user['items'][str(item_id)]['categoryID']
        category = user['categories'][category_id]
        category['rating'] = float(category['rating'] * category['votes'] + rating) / (category['votes'] + 1)
        category['votes'] += 1
        client.GRS.users.find_one_and_update({"_id": user_id},
                                             {'$set': {'categories': user['categories']}})  # todo optimize
    finally:
        client.close()
    # print category
    return category_id, category


def remove_all_items_from_category(user_id, category_id):
    client = DB.get_client()
    try:
        user = client.GRS.users.find_one({"_id": user_id})
        items = user['items']
        keys = items.keys()
        for key in keys:  # remove all items form specified category
            if items[key]['categoryID'] == category_id:
                items.__delitem__(key)
        client.GRS.users.find_one_and_update({"_id": user_id}, {'$set': {'items': items}})
    finally:
        client.close()


def rate_and_remove(user_id, item_id, rating):
    """
    change rating of category of item and remove items, where category rating <=0 and votes >=3
    :param user_id:
    :param item_id:
    :param rating:
    :return:
    """
    user_id = ObjectId(user_id)
    category_id, category = rate(user_id, item_id, rating)
    if category['votes'] >= 3 and category['rating'] <= 0:
        remove_all_items_from_category(user_id, category_id)



def get_number_of_pages(user_id):
    user_id = ObjectId(user_id)
    client = DB.get_client()
    try:
        user = client.GRS.users.find_one({"_id": user_id})
        assert user is not None
        return math.ceil(len(user['items'].keys()) / 100.0)
    finally:
        client.close()


def get_page(user_id, page_number):
    """
    return items from page
    :param user_id:
    :param page_number:
    :return: [] if wrong page, None if error, list of items if ok
    """
    # todo add items from RS
    assert type(page_number) == int
    if page_number <= 0:
        return []

    user_id = ObjectId(user_id)
    client = None
    try:
        client = DB.get_client()
        user = client.GRS.users.find_one({"_id": user_id})
        assert user is not None
        if (page_number - 1) * page_size >= len(user['items'].keys()):
            return []
        items = []
        # dict to list
        for key in sorted(user['items'].keys()):        # todo optimize
            user['items'][key].update({'itemId': key})
            items.append(user['items'][key])
        return items[page_size * (page_number - 1): page_size * page_number]
    finally:
        client.close()




