# from fastapi import Depends, HTTPException, status
# from open_webui.internal.db import get_db
# from open_webui.models.confidios.models import ConfidiosSession
# from open_webui.utils.auth import get_verified_user
# from sqlalchemy.orm import Session


# async def get_confidios_session(
#     user=Depends(get_verified_user), db: Session = Depends(get_db)
# ):
#     """
#     Get active Confidios session for the current user.
#     Raises HTTPException if no active session is found.
#     """
#     session = (
#         db.query(ConfidiosSession)
#         .filter(ConfidiosSession.user_id == user.id, ConfidiosSession.is_logged_in)
#         .first()
#     )

#     if not session:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Please login to Confidios first",
#         )

#     return session
