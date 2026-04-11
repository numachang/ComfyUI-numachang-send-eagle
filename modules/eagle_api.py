from typing import Optional, TypedDict, List
import requests


class FolderInfo(TypedDict):
    id: str
    name: str


class EagleAPI:
    def __init__(self, base_url="http://localhost:41595"):
        self.base_url = base_url
        self.folder_list: Optional[List[FolderInfo]] = None

    # #########################################
    # 画像をEagleに送信 (v2 /api/v2/item/add)
    # items: [{path, name, annotation, tags, ...}, ...]
    # 返り値: 作成されたアイテム ID のリスト
    def add_items(self, items: list, folder_id: Optional[str] = None) -> List[str]:
        if folder_id:
            for item in items:
                item.setdefault("folderId", folder_id)
        payload = {"items": items}
        resp = self._send_request("/api/v2/item/add", method="POST", data=payload)
        return resp.get("data", {}).get("ids", []) or []

    # #########################################
    # フォルダ名 or ID で該当フォルダを探してIDを返す
    # 存在しなければ作成してIDを返す
    def find_or_create_folder(self, name_or_id: str) -> str:
        folder = self._find_folder(name_or_id)
        if folder:
            return folder.get("id", "")
        return self._create_folder(name_or_id)

    # #########################################
    # フォルダ名 or ID で該当フォルダを取得
    def _find_folder(self, name_or_id: str) -> Optional[FolderInfo]:
        self._ensure_folder_list()
        if self.folder_list is not None:
            for folder in self.folder_list:
                if folder["name"] == name_or_id or folder["id"] == name_or_id:
                    return folder
        return None

    # #########################################
    # フォルダを作成 (v2 /api/v2/folder/create, name パラメータ)
    def _create_folder(self, name: str) -> str:
        if not name:
            return ""
        try:
            data = {"name": name}
            response = self._send_request("/api/v2/folder/create", method="POST", data=data)
            new_folder_id = (response.get("data") or {}).get("id", "")
            if new_folder_id and self.folder_list is not None:
                self.folder_list.append({"id": new_folder_id, "name": name})
            return new_folder_id
        except requests.RequestException:
            return ""

    # #########################################
    # Eagle のフォルダID、名前の一覧を取得 (v2)
    def _ensure_folder_list(self):
        if self.folder_list is None:
            self._get_all_folder_list()

    def _get_all_folder_list(self):
        try:
            json = self._send_request("/api/v2/folder/get")
            data = json.get("data", [])
            # v2 はページングラッパ {data: [...], total, offset, limit}
            if isinstance(data, dict):
                data = data.get("data", [])
            self.folder_list = self._extract_id_name_pairs(data)
        except requests.RequestException:
            self.folder_list = []

    # #########################################
    # Private method for sending requests
    def _send_request(self, endpoint, method="GET", data=None):
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json"}

        try:
            if method == "GET":
                response = requests.get(url, headers=headers)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            print(f"Eagle request failed: {e}")
            raise

    # #########################################
    # フォルダリストをツリーから平坦化
    def _extract_id_name_pairs(self, data):
        result = []

        def recursive_extract(item):
            if isinstance(item, dict):
                if "id" in item and "name" in item:
                    result.append({"id": item["id"], "name": item["name"]})
                if "children" in item and isinstance(item["children"], list):
                    for child in item["children"]:
                        recursive_extract(child)
            elif isinstance(item, list):
                for element in item:
                    recursive_extract(element)

        recursive_extract(data)
        return result
